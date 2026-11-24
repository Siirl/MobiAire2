/* MobiAire Materno — cliente móvil (app Android / iOS).
   Modelo cliente-servidor: este cliente NUNCA llama a la API de WAQI; solo pide
   datos al servidor (que es el conmutador y guarda todo en su base de datos).
   Ya no es una PWA: no hay service worker ni manifest ni botón "Instalar". */
"use strict";

var DATOS = [];          // estaciones (mapa/lista)
var ZONAS_ASOC = null;   // tabla de asociación zona->estación
var RECOS = null;        // textos editables del PO
var mapa = null, marcadores = {}, mapaDibujado = false;
var TOKEN = "", USUARIO = null;
var zonaLayers = [], ubicMarker = null;
var NOTIF_LAST = 0, NOTIF_TIMER = null, NOTIF_VISTAS = {};
var MAPA_TIMER = null, SYNC_TIMER = null, ULTIMO_ESTADO_HASH = "";
var UBIC_LISTENERS = [], ULTIMO_ENVIO_UBIC = 0, ubicActual = null;

function $(id){ return document.getElementById(id); }
function esc(t){ var d=document.createElement("div"); d.textContent=(t==null?"":""+t); return d.innerHTML; }
function nivelClase(n){ return (n==="verde"||n==="naranja"||n==="rojo")?n:"gris"; }
function colorNivel(n){ return n==="verde"?"#2f8f54":n==="naranja"?"#c9780f":n==="rojo"?"#b3362f":"#6b7280"; }
function haversine(a,b,c,d){var R=6371,r=Math.PI/180,dla=(c-a)*r,dlo=(d-b)*r,
  s=Math.sin(dla/2)**2+Math.cos(a*r)*Math.cos(c*r)*Math.sin(dlo/2)**2;return 2*R*Math.asin(Math.sqrt(s));}

function haceCuanto(iso){
  if(!iso) return "";
  var t=new Date(iso); if(isNaN(t)) return "";
  var min=Math.round((Date.now()-t.getTime())/60000);
  if(min<0) min=0;
  if(min<60) return "Hace "+min+" min";
  var h=Math.round(min/60);
  return "Hace "+h+" h";
}

var ICONO_NIVEL = {
  verde:'<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M3 8.5l3.2 3.2L13 5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  naranja:'<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M8 2l6.5 11.5H1.5z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M8 6.5v3.2M8 11.6v.1" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
  rojo:'<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M8 4.6v4M8 10.6v.1" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
  gris:'<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M5.5 8h5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>'
};
var ETIQUETA_NIVEL = { verde:"Aire aceptable", naranja:"Precaución", rojo:"Aire dañino hoy", gris:"Sin dato reciente" };
var NOMBRE_CONT = { pm25:"PM2.5", pm10:"PM10", no2:"NO₂", so2:"SO₂", o3:"O₃", co:"CO" };

/* ============ Sesión ============ */
function guardarSesion(token, user){
  TOKEN = token || "";
  USUARIO = user || null;
  try{
    if(TOKEN){ localStorage.setItem("mobiaire_token", TOKEN); localStorage.setItem("mobiaire_user", JSON.stringify(USUARIO)); }
    else { localStorage.removeItem("mobiaire_token"); localStorage.removeItem("mobiaire_user"); }
  }catch(e){}
  try{ sincronizarServicioNativo(); }catch(e){}
}
function recuperarSesion(){
  try{
    var t = localStorage.getItem("mobiaire_token");
    var u = localStorage.getItem("mobiaire_user");
    if(t){ TOKEN = t; USUARIO = u ? JSON.parse(u) : null; }
  }catch(e){}
}
function cerrarSesionRemota(){
  guardarSesion("","");
  renderPerfil();
  alert("Tu sesión se cerró porque se inició sesión en otro dispositivo.");
}
function authHeaders(){
  var h = {"Content-Type":"application/json"};
  if(TOKEN) h["Authorization"]="Bearer "+TOKEN;
  return h;
}
function mensajeErrorNatural(raw){
  if(!raw) return "Ocurrió un inconveniente. Por favor, inténtalo de nuevo.";
  var txt = (typeof raw === "string") ? raw : (raw.message || String(raw));
  try{
    var j = JSON.parse(txt);
    if(j && j.error) txt = j.error;
  }catch(e){}
  var low = txt.toLowerCase();
  if(low.indexOf("credenciales")>=0 || low.indexOf("401")>=0 || low.indexOf("invalid")>=0){
    return "El usuario o la contraseña son incorrectos. Por favor verifícalos e inténtalo de nuevo.";
  }
  if(low.indexOf("ya existe")>=0 || low.indexOf("409")>=0){
    return "Ese nombre de usuario ya está registrado. Por favor elige otro.";
  }
  if(low.indexOf("requerido")>=0 || low.indexOf(">=4")>=0 || low.indexOf("400")>=0){
    return "Ingresa un nombre de usuario y una contraseña de al menos 4 caracteres.";
  }
  if(low.indexOf("failed to fetch")>=0 || low.indexOf("network")>=0){
    return "No pudimos conectarnos con el servidor. Verifica tu conexión.";
  }
  return txt;
}

async function api(path, opts){
  opts = opts || {};
  opts.headers = Object.assign(authHeaders(), opts.headers||{});
  var r = await fetch(path, opts);
  if(r.status===401 && TOKEN && path.indexOf("/api/auth/login")<0){ cerrarSesionRemota(); throw new Error("Tu sesión ha finalizado."); }
  if(!r.ok){
    var rawText = await r.text();
    throw new Error(mensajeErrorNatural(rawText));
  }
  return r.json();
}

/* ============ Puente nativo (Android) ============ */
function bridge(){ return (window.AndroidBridge ? window.AndroidBridge : null); }
window.mobiaireUbicacion = function(lat, lon){
  var pos = {lat: parseFloat(lat), lon: parseFloat(lon)};
  ubicActual = pos;
  if(window.__ubiResolve){ var r=window.__ubiResolve; window.__ubiResolve=null; window.__ubiReject=null; r(pos); }
  UBIC_LISTENERS.forEach(function(f){ try{ f(pos); }catch(e){} });
};
window.mobiaireUbicacionError = function(msg){
  if(window.__ubiReject){ var j=window.__ubiReject; window.__ubiResolve=null; window.__ubiReject=null; j(new Error(msg||"permiso denegado")); }
};
window.mobiaireContacto = function(json){
  var r=window.__contactoResolve; window.__contactoResolve=null;
  if(!r) return;
  try{ r(json?JSON.parse(json):null); }catch(e){ r(null); }
};

/* La app nativa puede pedir abrir una pestaña (p. ej. al tocar una notificación). */
window.mobiaireAbrirVista = function(v){
  try{ cambiarVista(v||"inicio"); }catch(e){}
};

/* Mantiene el servicio en segundo plano al día con la sesión actual. */
function sincronizarServicioNativo(){
  var ab=bridge(); if(!ab) return;
  try{
    if(ab.guardarToken) ab.guardarToken(TOKEN||"", location.origin);
    if(ab.iniciarServicio) ab.iniciarServicio();
  }catch(e){}
}

/* Devuelve una Promesa con {lat,lon}: primero el puente nativo, luego la web. */
function getUbicacionActual(){
  return new Promise(function(resolve, reject){
    var ab = bridge();
    if(ab && ab.obtenerUbicacion){
      try{
        var s = ab.obtenerUbicacion();
        if(s){ var o = JSON.parse(s); if(o && o.lat!=null){ resolve({lat:o.lat, lon:o.lon}); return; } }
      }catch(e){}
      window.__ubiResolve = resolve; window.__ubiReject = reject;
      try{ ab.pedirUbicacion(); }catch(e){}
      setTimeout(function(){
        if(window.__ubiResolve){ var j=window.__ubiReject; window.__ubiResolve=null; window.__ubiReject=null; if(j) j(new Error("sin ubicación")); }
      }, 20000);
      return;
    }
    if(navigator.geolocation){
      navigator.geolocation.getCurrentPosition(
        function(p){ resolve({lat:p.coords.latitude, lon:p.coords.longitude}); },
        function(e){ reject(e); }, {enableHighAccuracy:true, timeout:20000, maximumAge:5000});
      return;
    }
    reject(new Error("sin geolocalización"));
  });
}
function onUbicacion(handler){ UBIC_LISTENERS.push(handler); if(ubicActual) try{ handler(ubicActual); }catch(e){} }
function iniciarUbicacionContinua(){
  var ab = bridge();
  if(ab && ab.iniciarUbicacion){ try{ ab.iniciarUbicacion(); return; }catch(e){} }
  if(navigator.geolocation){
    navigator.geolocation.watchPosition(function(p){
      window.mobiaireUbicacion(p.coords.latitude, p.coords.longitude);
    }, function(){}, {enableHighAccuracy:true, maximumAge:10000});
  }
}
function enviarUbicacionServidor(lat, lon, forzar){
  if(!TOKEN) return;
  var ahora = Date.now();
  if(!forzar && ULTIMO_ENVIO_UBIC > 0 && ahora - ULTIMO_ENVIO_UBIC < 60000) return;   // cada 1 min o al forzar
  ULTIMO_ENVIO_UBIC = ahora;
  api("/api/users/me/ubicacion",{method:"POST",body:JSON.stringify({lat:lat,lon:lon})}).catch(function(){});
}
function elegirContactoNativo(){
  return new Promise(function(resolve){
    var ab = bridge();
    if(!ab || !ab.elegirContacto){ resolve(null); return; }
    window.__contactoResolve = resolve;
    try{ ab.elegirContacto(); }catch(e){ resolve(null); }
    setTimeout(function(){ if(window.__contactoResolve){ window.__contactoResolve=null; resolve(null); } }, 90000);
  });
}

/* ============ Textos editables ============ */
function cargarRecos(){
  return fetch("/recomendaciones.json").then(function(r){return r.ok?r.json():null;})
    .then(function(j){ RECOS=j; }).catch(function(){ RECOS=null; });
}
function recoDe(nivel){
  var n = nivelClase(nivel);
  if(RECOS && RECOS.niveles && RECOS.niveles[n]) return RECOS.niveles[n].recomendacion;
  return n==="gris" ? "Sin dato reciente, no podemos orientarte ahora."
    : "Consulta la guía para saber qué hacer con este nivel de aire.";
}

/* Recomendación de la pantalla de inicio: texto claro + tabla + viñetas. */
function pintarRecoSimple(nivel){
  var el=$("reco-txt"); if(!el) return;
  var n=nivelClase(nivel);
  var r=(RECOS && RECOS.niveles && RECOS.niveles[n]) || null;
  var titulo=(r&&r.titulo)||ETIQUETA_NIVEL[n]||"Sin dato";
  var txt = r ? r.recomendacion : recoDe(n);
  el.innerHTML =
    '<dl class="reco__meta"><dt>Nivel de aire</dt><dd>'+esc(titulo)+'</dd>'+
    '<dt>Tu zona</dt><dd>'+esc(($("zona-nombre")&&$("zona-nombre").textContent)||"—")+'</dd></dl>'+
    '<p class="reco__lead">'+esc(txt)+'</p>'+
    '<div class="reco__bloque"><b>¿Y en mi embarazo?</b>'+
    'Inicia sesión y completa tu perfil (semanas y condiciones) para recibir avisos hechos a tu medida.</div>';
}
function pintarRecoPersonalizada(rec){
  var el=$("reco-txt"); if(!el || !rec) return;
  var n=nivelClase(rec.nivel);
  var partes=[];
  var filas=[["Nivel de aire", ETIQUETA_NIVEL[n]||"—"]];
  if(rec.semanas!=null) filas.push(["Semanas", "Semana "+rec.semanas+" · trimestre "+rec.trimestre]);
  if(rec.estacion && rec.estacion.nombre) filas.push(["Estación", rec.estacion.nombre]);
  partes.push('<dl class="reco__meta">'+filas.map(function(f){
    return '<dt>'+esc(f[0])+'</dt><dd>'+esc(f[1])+'</dd>';}).join('')+'</dl>');
  if(rec.base) partes.push('<p class="reco__lead">'+esc(rec.base)+'</p>');
  var items=[];
  if(rec.por_semana) items.push(rec.por_semana);
  (rec.por_condiciones||[]).forEach(function(t){ items.push(t); });
  if(rec.contaminantes) items.push(rec.contaminantes);
  (rec.comentarios||[]).forEach(function(t){ items.push(t); });
  if(items.length){
    partes.push('<ul class="reco__lista">'+items.map(function(t){
      return '<li>'+esc(t)+'</li>';}).join('')+'</ul>');
  }
  el.innerHTML = partes.join("");
}

/* ============ Tarjeta principal ============ */
function pintarZona(d){
  var card=$("tarjeta-zona");
  var zonaNombre = (d && d.zona && d.zona.nombre) || "tu zona";
  $("zona-nombre").textContent = zonaNombre;
  $("franja-zona-nombre").textContent = zonaNombre;
  $("zona-otros").hidden = true; $("zona-otros").innerHTML = ""; $("zona-otros-aviso").hidden = true;

  if(d && d.estado==="pendiente"){
    card.className="tarjeta principal nivel-gris";
    $("zona-estacion").textContent = (d.zona&&d.zona.motivo) || "Asociación de zona pendiente.";
    setChip("gris");
    $("zona-medidas").hidden=true; $("zona-pie").hidden=true; $("zona-fuente").hidden=true;
    pintarRecoSimple("gris");
    return;
  }
  if(!d || !d.semaforo || d.estado==="sin_dato_reciente"){
    card.className="tarjeta principal nivel-gris";
    $("zona-estacion").textContent = "Estación: " + ((d&&d.estacion&&d.estacion.nombre)||"—") + " · sin dato de las últimas horas.";
    setChip("gris");
    $("zona-medidas").hidden=true; $("zona-pie").hidden=true; $("zona-fuente").hidden=true;
    pintarRecoSimple("gris");
    return;
  }

  var s=d.semaforo, n=nivelClase(s ? s.nivel : "gris");
  card.className="tarjeta principal nivel-"+n;
  $("zona-estacion").textContent = "Estación: " + ((d.estacion&&d.estacion.nombre)||"—")
      + (d.zona&&d.zona.distancia_km!=null ? " · "+d.zona.distancia_km+" km" : "");
  setChip(n);

  // Exact WAQI sub-indices
  var pm25 = (d.aqi_pm25 != null) ? d.aqi_pm25 : ((d.estacion && d.estacion.aqi_pm25 != null) ? d.estacion.aqi_pm25 : (s ? s.aqi_pm25 : null));
  var pm10 = (d.aqi_pm10 != null) ? d.aqi_pm10 : ((d.estacion && d.estacion.aqi_pm10 != null) ? d.estacion.aqi_pm10 : (s ? s.aqi_pm10 : null));
  if(pm25 == null && s && s.pm25_estimado_ugm3 != null) pm25 = s.pm25_estimado_ugm3;
  if(pm10 == null && s && s.pm10_estimado_ugm3 != null) pm10 = s.pm10_estimado_ugm3;

  pintarMedida("md-pm25", "PM2.5", pm25, false);
  pintarMedida("md-pm10", "PM10", pm10, false);
  $("zona-medidas").hidden=false;
  pintarOtrosContaminantes(d);

  var valorIcaAqi = (d.indice_waqi != null) ? d.indice_waqi : ((d.estacion && d.estacion.aqi_waqi != null) ? d.estacion.aqi_waqi : (s ? s.ica : "—"));
  $("zona-ica").innerHTML = 'AQI / ICA <b>'+esc(valorIcaAqi)+'</b><small>WAQI</small>';
  var iso = d.estacion && (d.estacion.medido_iso || d.estacion.medido_bogota);
  $("zona-actualizado").textContent = haceCuanto(iso) || (d.estacion&&d.estacion.medido_texto) || "";
  $("zona-pie").hidden=false;

  $("zona-fuente").hidden=false;
  $("zona-fuente").innerHTML = "Datos en tiempo real de la estación WAQI. "
      + "Índice WAQI: "+esc(valorIcaAqi)+". Fuente: WAQI · "+esc((d.atribucion||[]).join(" · "));

  $("reco-txt").innerHTML = "";
  pintarRecoSimple(n);
  aplicarTema(n);
  actualizarRecoPersonalizada();
}
function setChip(n){
  var chip=$("zona-chip");
  chip.className="chip-estado nivel-"+n;
  chip.innerHTML = (ICONO_NIVEL[n]||"") + '<span id="zona-chip-txt">'+esc(ETIQUETA_NIVEL[n])+'</span>';
}
function pintarMedida(id, k, v, esUgm3){
  var el=$(id);
  if(v==null){ el.className="medida medida--na"; el.innerHTML='<div class="medida__k">'+k+'</div><div class="medida__v">N/D</div>'; return; }
  el.className="medida";
  var unidad = esUgm3 ? ' <small>µg/m³ est.</small>' : ' <small>sub-índice WAQI</small>';
  el.innerHTML='<div class="medida__k">'+k+'</div><div class="medida__v">'+esc(v)+unidad+'</div>';
}
function pintarOtrosContaminantes(d){
  var cont = $("zona-otros"), aviso = $("zona-otros-aviso");
  var pares = [
    ["no2", (d.aqi_no2 != null) ? d.aqi_no2 : ((d.estacion && d.estacion.aqi_no2 != null) ? d.estacion.aqi_no2 : d.no2)],
    ["so2", (d.aqi_so2 != null) ? d.aqi_so2 : ((d.estacion && d.estacion.aqi_so2 != null) ? d.estacion.aqi_so2 : d.so2)],
    ["o3", (d.aqi_o3 != null) ? d.aqi_o3 : ((d.estacion && d.estacion.aqi_o3 != null) ? d.estacion.aqi_o3 : d.o3)],
    ["co", (d.aqi_co != null) ? d.aqi_co : ((d.estacion && d.estacion.aqi_co != null) ? d.estacion.aqi_co : d.co)]
  ];
  var hay = pares.filter(function(p){return p[1]!=null;});
  var faltan = pares.filter(function(p){return p[1]==null;}).map(function(p){return NOMBRE_CONT[p[0]];});
  cont.innerHTML = hay.map(function(p){
    return '<div class="medida"><div class="medida__k">'+NOMBRE_CONT[p[0]]+'</div><div class="medida__v">'+esc(p[1])+' <small>sub-índice WAQI</small></div></div>';
  }).join("");
  cont.hidden = hay.length===0;
  if(faltan.length){
    aviso.hidden=false;
    aviso.textContent = "La estación no reporta: " + faltan.join(", ") + ".";
  } else { aviso.hidden=true; }
}

/* ============ Zonas y Estaciones ============ */
function cargarZonas(){
  var sel=$("sel-zona");
  if(!sel) return Promise.resolve();
  sel.innerHTML = '<option value="">— Elige una estación o zona —</option>';
  return Promise.all([
    fetch("/api/waqi/estaciones").then(function(r){return r.ok?r.json():null;}).catch(function(){return null;}),
    fetch("/api/zonas/asociacion").then(function(r){return r.ok?r.json():null;}).catch(function(){return null;})
  ]).then(function(res){
    var dEst = res[0];
    var dZon = res[1];

    // 1. Estaciones de monitoreo WAQI (Bogotá y alrededores)
    var ests = (dEst && dEst.estaciones) || [];
    if(ests.length){
      var grpEst = document.createElement("optgroup");
      grpEst.label = "Estaciones WAQI (Bogotá y alrededores)";
      ests.forEach(function(e){
        var sem = e.semaforo ? (" [" + (ETIQUETA_NIVEL[e.semaforo.nivel] || e.semaforo.nivel) + "]") : (e.reciente ? "" : " [sin dato]");
        var aqiTxt = (e.aqi_waqi != null) ? (" · AQI " + e.aqi_waqi) : "";
        var opt = new Option(e.nombre + sem + aqiTxt, String(e.uid));
        grpEst.appendChild(opt);
      });
      sel.appendChild(grpEst);
    }

    // 2. Zonas UPZ de Ciudad Bolívar
    var zonas = (dZon && dZon.zonas) || [];
    if(zonas.length){
      ZONAS_ASOC = dZon;
      var grpZon = document.createElement("optgroup");
      grpZon.label = "Zonas UPZ (Ciudad Bolívar)";
      zonas.forEach(function(zo){
        grpZon.appendChild(new Option(zo.nombre, zo.id));
      });
      sel.appendChild(grpZon);
    }
  }).catch(function(){
    return fetch("/api/zonas").then(function(r){return r.json();}).then(function(z){
      (z.zonas||[]).forEach(function(zo){ sel.add(new Option(zo.nombre, zo.id)); });
    }).catch(function(){});
  }).finally(function(){
    var h=(location.hash||"").replace(/^#zona=/,"");
    var inicial = h || (USUARIO && USUARIO.profile && USUARIO.profile.zona) || "";
    if(inicial){ sel.value=inicial; verZona(inicial); }
  });
}
function verZona(id){
  $("zona-estacion").textContent = "Consultando…";
  fetch("/api/zona/"+encodeURIComponent(id)).then(function(r){
    if(r.status===503) throw {pend:true};
    if(!r.ok) throw {codigo:r.status};
    return r.json();
  }).then(function(d){ pintarZona(d); })
  .catch(function(err){
    var card=$("tarjeta-zona"); card.className="tarjeta principal nivel-gris";
    setChip("gris"); $("zona-medidas").hidden=true; $("zona-pie").hidden=true; $("zona-fuente").hidden=true;
    $("zona-estacion").textContent = err&&err.pend
      ? "No disponible ahora (el servidor aún no tiene datos de WAQI)."
      : "No se pudo consultar la zona ahora.";
    pintarRecoSimple("gris");
  });
}

/* ============ Estaciones ============ */
function mapearEstaciones(d){
  return (d.estaciones||[]).map(function(e){
    var rec = Boolean(e.reciente);
    var niv = (rec && e.semaforo && e.semaforo.nivel) ? e.semaforo.nivel : (e.nivel_forzado || "gris");
    if(!rec) niv = "gris";
    return {
      uid:e.uid, nombre:e.nombre, lat:e.lat, lon:e.lon, aqi:e.aqi_waqi,
      hora:e.medido_texto||e.medido_bogota||"Sin actualización reciente",
      reciente:rec,
      nivel:niv,
      icaEst:(rec && e.semaforo) ? e.semaforo.ica : null,
      semaforo: rec ? e.semaforo : null,
      aqi_pm25: e.aqi_pm25, aqi_pm10: e.aqi_pm10,
      no2: e.aqi_no2, so2: e.aqi_so2, o3: e.aqi_o3, co: e.aqi_co,
      atribucion: e.atribucion || [],
      medido_iso: e.medido_iso, medido_bogota: e.medido_bogota, medido_texto: e.medido_texto,
      demo:false
    };
  });
}
function cargarEstaciones(){
  return fetch("/api/waqi/estaciones").then(function(r){
    if(!r.ok) throw new Error("waqi "+r.status); return r.json();
  }).then(function(d){
    var cinta = $("prueba-cinta"); if(cinta) cinta.hidden = !d.modo_prueba;
    var lista = mapearEstaciones(d);
    if(!lista.length){ throw new Error(d.aviso || "vacío"); }
    DATOS = lista;
    aplicarDemoCinta(false);
    pintarLista();
    actualizarEstacionCercanaEnInicio();
  }).catch(function(){
    DATOS=[
      {uid:"d1",nombre:"Kennedy (ej.)",lat:4.6249,lon:-74.16135,aqi:28,nivel:"verde",icaEst:28,hora:"ejemplo",reciente:true,demo:true},
      {uid:"d2",nombre:"Tunal (ej.)",lat:4.57619,lon:-74.13093,aqi:42,nivel:"naranja",icaEst:42,hora:"ejemplo",reciente:true,demo:true},
      {uid:"d3",nombre:"Carvajal (ej.)",lat:4.5958,lon:-74.14855,aqi:80,nivel:"naranja",icaEst:76,hora:"ejemplo",reciente:true,demo:true},
      {uid:"d4",nombre:"Ejemplo Rojo",lat:4.60,lon:-74.10,aqi:115,nivel:"rojo",icaEst:115,hora:"ejemplo",reciente:true,demo:true}
    ];
    aplicarDemoCinta(true);
    pintarLista();
    actualizarEstacionCercanaEnInicio();
  });
}
function aplicarDemoCinta(esDemo){ var c=$("demo-cinta"); if(c) c.hidden=!esDemo; }

function pintarLista(){
  var cont=$("listado"); if(!cont) return; cont.innerHTML="";
  DATOS.forEach(function(e){
    var n = e.reciente ? nivelClase(e.nivel) : "gris";
    var color = e.demo ? "#6b7280" : colorNivel(n);
    var nivelTxt = (e.reciente && e.nivel && e.nivel!=="gris")
      ? (ETIQUETA_NIVEL[n]+(e.icaEst!=null?" · ICA est. "+e.icaEst:""))
      : "Sin dato reciente (gris)";
    var b=document.createElement("button");
    b.className="est"; b.setAttribute("aria-pressed","false"); b.dataset.uid=e.uid;
    b.innerHTML='<span class="est__p" style="background:'+color+'"></span>'+
      '<span><span class="est__n">'+esc(e.nombre)+'</span>'+
      '<span class="est__m">'+esc(nivelTxt)+' · índice WAQI '+esc(e.aqi==null?"—":e.aqi)+(e.demo?' · DATOS SIMULADOS':(e.hora?' · '+esc(e.hora):''))+'</span></span>'+
      '<span class="est__i" style="color:'+color+'">'+esc(e.aqi==null?"—":e.aqi)+'</span>';
    b.onclick=function(){ seleccionarEst(e.uid); };
    cont.appendChild(b);
  });
}
function seleccionarEst(uid){
  document.querySelectorAll(".est").forEach(function(x){ x.setAttribute("aria-pressed", x.dataset.uid==uid?"true":"false"); });
  var e=DATOS.find(function(x){return ""+x.uid===""+uid;});
  if(e && marcadores[uid] && mapa){ mapa.setView([e.lat,e.lon],13); marcadores[uid].openPopup(); }
}
function pintarMarcadores(){
  if(!mapa || !window.L) return;
  Object.keys(marcadores).forEach(function(k){ try{ mapa.removeLayer(marcadores[k]); }catch(e){} });
  marcadores = {};
  DATOS.forEach(function(e){
    if(e.lat==null||e.lon==null) return;
    var n = e.reciente ? nivelClase(e.nivel) : "gris";
    var color=e.demo?"#6b7280":colorNivel(n);
    var m=L.circleMarker([e.lat,e.lon],{radius:9,color:"#fff",weight:2,fillColor:color,fillOpacity:1}).addTo(mapa);
    var sem=(e.reciente && e.nivel && e.nivel!=="gris")
      ? ('Semáforo: <b>'+esc(ETIQUETA_NIVEL[n])+'</b>'+(e.icaEst!=null?' (ICA est. '+esc(e.icaEst)+')':''))
      : '<b>Sin dato reciente (estación en gris)</b>';
    m.bindPopup('<b>'+esc(e.nombre)+'</b><br>'+sem+'<br>índice WAQI '+esc(e.aqi==null?"—":e.aqi)+' <em>(US-EPA)</em>'+(e.demo?'<br><em>DATOS SIMULADOS</em>':''));
    m.on("click",function(){ seleccionarEst(e.uid); });
    marcadores[e.uid]=m;
  });
}
function dibujarMapa(){
  if(mapaDibujado || !window.L) return;
  try{
    mapa=L.map("mapa").setView([4.62,-74.13],11);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",{maxZoom:18,attribution:'© OpenStreetMap contributors'}).addTo(mapa);
    pintarMarcadores();
    mapaDibujado=true;
    dibujarZonasMapa();
  }catch(err){}
}
/* Relee estaciones y zonas del servidor y repinta el mapa SIN recargar la app.
   Así el color cambia al instante (p. ej. tras cambiar el modo prueba). */
function refrescarMapa(){
  if(!mapa) return;
  fetch("/api/waqi/estaciones").then(function(r){return r.ok?r.json():null;}).then(function(d){
    if(d){
      var cinta=$("prueba-cinta"); if(cinta) cinta.hidden=!d.modo_prueba;
      var lista=mapearEstaciones(d);
      if(lista.length){ DATOS=lista; pintarLista(); }
    }
  }).catch(function(){}).then(function(){
    pintarMarcadores();
    dibujarZonasMapa();
  });
}

/* ============ Guía ============ */
function pintarGuia(){
  var man=$("guia-manual");
  var pasos=(RECOS&&RECOS.manual)||[];
  if(man){
    man.innerHTML = pasos.length ? pasos.map(function(p){
      return '<div class="paso"><span class="paso__n">'+esc(p.icono||"•")+'</span><div>'+
        '<h3>'+esc(p.titulo)+'</h3><p>'+esc(p.texto)+'</p>'+
        ((p.lista&&p.lista.length)?('<ul>'+p.lista.map(function(x){return '<li>'+esc(x)+'</li>';}).join("")+'</ul>'):'')+
        '</div></div>';
    }).join("") : '<div class="aviso-estado">No se pudo cargar el manual.</div>';
  }
  var cont=$("guia-cont");
  var items=(RECOS&&RECOS.guia)||[];
  if(!items.length){ cont.innerHTML='<div class="aviso-estado">No se pudo cargar la guía.</div>'; return; }
  cont.innerHTML = '<table class="tabla"><thead><tr><th>Nivel</th><th>Qué significa</th><th>Qué hacer</th></tr></thead><tbody>'+
    items.map(function(g){
      var n=nivelClase(g.nivel);
      return '<tr><td><span class="pt" style="background:'+colorNivel(n)+'"></span><b>'+esc(g.nivel)+'</b><br><small>'+esc(g.rango)+'</small></td>'+
        '<td>'+esc(g.que_significa)+'</td><td>'+esc(g.que_hacer)+'</td></tr>';
    }).join("")+'</tbody></table>';
}

/* ============ Navegación ============ */
function cambiarVista(v){
  var map={inicio:"vista-inicio",estaciones:"vista-estaciones",guia:"vista-guia",
           acerca:"vista-acerca",info:"vista-info",indicaciones:"vista-indicaciones"};
  Object.keys(map).forEach(function(k){ $(map[k]).hidden = (k!==v); });
  document.querySelectorAll(".nav__b").forEach(function(b){ b.setAttribute("aria-current", b.dataset.vista===v?"true":"false"); });
  if(v==="estaciones"){
    setTimeout(function(){
      dibujarMapa(); if(mapa) mapa.invalidateSize();
      refrescarMapa();               // sincroniza colores al instante
    }, 60);
    if(MAPA_TIMER) clearInterval(MAPA_TIMER);
    MAPA_TIMER = setInterval(function(){ if(mapa) refrescarMapa(); }, 60000);
  } else if(MAPA_TIMER){ clearInterval(MAPA_TIMER); MAPA_TIMER=null; }
  if(v==="guia"){ pintarGuia(); }
  if(v==="info"){ pintarInfo(); }
  if(v==="indicaciones"){ verIndicaciones(); }
  window.scrollTo(0,0);
}

/* ============ Selector de zona ============ */
function abrirHoja(){ $("hoja-fondo").hidden=false; $("hoja-zona").hidden=false; $("sel-zona").focus(); }
function cerrarHoja(){ $("hoja-fondo").hidden=true; $("hoja-zona").hidden=true; }

function usarMiUbicacion(){
  var aviso=$("zona-ubic-aviso");
  aviso.textContent="Pidiendo ubicación… (si la niegas, elige la zona o estación manualmente).";
  getUbicacionActual().then(function(pos){
    var la=pos.lat, lo=pos.lon;
    enviarUbicacionServidor(la, lo);

    // Buscar estación WAQI más cercana en DATOS
    var mejorEst = null, dminEst = 1e9;
    if(DATOS && DATOS.length){
      DATOS.forEach(function(e){
        if(e.lat==null || e.lon==null) return;
        var d = haversine(la, lo, e.lat, e.lon);
        if(d < dminEst){ dminEst = d; mejorEst = e; }
      });
    }

    // Buscar también en ZONAS_ASOC si existe
    var mejorZona = null, dminZona = 1e9;
    if(ZONAS_ASOC && ZONAS_ASOC.zonas){
      ZONAS_ASOC.zonas.forEach(function(z){
        if(z.estado!=="ok" || !z.estacion) return;
        var d = haversine(la, lo, z.estacion.lat, z.estacion.lon);
        if(d < dminZona){ dminZona = d; mejorZona = z; }
      });
    }

    if(mejorEst && mejorEst.uid != null){
      var targetId = String(mejorEst.uid);
      if($("sel-zona")) $("sel-zona").value = targetId;
      location.hash = "zona=" + targetId;
      verZona(targetId);
      guardarZonaEnPerfil(targetId);
      aviso.textContent = "Estación más cercana: " + mejorEst.nombre + " (" + (Math.round(dminEst*10)/10) + " km).";
    } else if(mejorZona){
      if($("sel-zona")) $("sel-zona").value = mejorZona.id;
      location.hash = "zona=" + mejorZona.id;
      verZona(mejorZona.id);
      guardarZonaEnPerfil(mejorZona.id);
      aviso.textContent = "Zona sugerida por cercanía: " + mejorZona.nombre + ".";
    } else {
      aviso.textContent = "No se encontraron estaciones cercanas. Elige manualmente.";
    }
  }).catch(function(){ aviso.textContent="Permiso denegado. Elige la estación o zona manualmente (la app sigue funcionando)."; });
}

/* ============ Perfil / autenticación / admin ============ */
function abrirModal(idHoja, idFondo){ $(idFondo).hidden=false; $(idHoja).hidden=false; }
function cerrarModal(idHoja, idFondo){ $(idFondo).hidden=true; $(idHoja).hidden=true; }
function abrirPerfil(){ abrirModal("hoja-perfil","modal-perfil"); renderPerfil(); }

function guardarZonaEnPerfil(zonaId){
  if(!TOKEN || !USUARIO) return;
  USUARIO.profile = Object.assign({}, USUARIO.profile, {zona: zonaId});
  guardarSesion(TOKEN, USUARIO);
  api("/api/users/me/profile",{method:"PUT",body:JSON.stringify({profile:{zona:zonaId}})}).catch(function(){});
}

function renderPerfil(){
  var cont = $("perfil-cont");
  if(!cont) return;
  if(!TOKEN || !USUARIO){ renderLogin(cont); return; }
  if(USUARIO.role==="admin"){ $("perfil-tit").textContent = "Panel administrador"; renderAdmin(cont); return; }
  $("perfil-tit").textContent = "Mi perfil";
  renderPerfilGestante(cont);
}

function renderLogin(cont){
  $("perfil-tit").textContent = "MobiAire Materno";
  cont.innerHTML =
    '<div class="auth-cab"><img src="/iconos/icono.svg" alt=""><p class="hoja__sub" style="margin-top:6px">Entra para guardar tu perfil y recibir avisos personalizados, o explora la app sin cuenta.</p></div>'+
    '<div class="campo"><label>Usuario</label><input id="pf-user" type="text" autocomplete="username" placeholder="Tu nombre de usuario"></div>'+
    '<div class="campo"><label>Contraseña</label><input id="pf-pass" type="password" autocomplete="current-password" placeholder="Tu contraseña"></div>'+
    '<div class="acciones">'+
    '<button class="btn-prim" id="btn-login" type="button">Entrar</button>'+
    '<button class="btn-sec" id="btn-reg" type="button">Registrarme</button>'+
    '<button class="btn-sec" id="btn-explorar" type="button">Explorar app</button></div>'+
    '<p id="pf-msg" class="msg-err" style="margin-top:10px"></p>';
  $("btn-login").onclick = async function(){
    var uVal = ($("pf-user").value||"").trim();
    var pVal = $("pf-pass").value||"";
    if(!uVal || !pVal){
      $("pf-msg").textContent = "Por favor escribe tu usuario y contraseña para continuar.";
      return;
    }
    try{
      var d = await api("/api/auth/login",{method:"POST",body:JSON.stringify({username:uVal,password:pVal})});
      guardarSesion(d.token,d.user);
      if(d.sesion_previa_cerrada){ alert("Ya tenías una sesión abierta; se cerró la anterior."); }
      cerrarModal("hoja-perfil","modal-perfil");
      iniciarNotificaciones();
      if(ubicActual) enviarUbicacionServidor(ubicActual.lat, ubicActual.lon, true);
      if(d.user && d.user.profile && d.user.profile.zona && $("sel-zona")){
        $("sel-zona").value = d.user.profile.zona;
        verZona(d.user.profile.zona);
      } else {
        actualizarEstacionCercanaEnInicio();
      }
      cambiarVista("inicio");
      renderPerfil();
    }catch(e){
      $("pf-msg").textContent = mensajeErrorNatural(e);
    }
  };
  $("btn-reg").onclick = function(){
    renderRegistro(cont, ($("pf-user")&&$("pf-user").value)||"", ($("pf-pass")&&$("pf-pass").value)||"");
  };
  if($("btn-explorar")) $("btn-explorar").onclick = function(){ cerrarModal("hoja-perfil","modal-perfil"); };
}

function renderRegistro(cont, userInicial, passInicial){
  $("perfil-tit").textContent = "Crear cuenta de usuaria";
  var condOpts = [
    {v:"asma",t:"Asma o problemas respiratorios"},
    {v:"hipertension",t:"Hipertensión o antecedentes de preeclampsia"},
    {v:"diabetes",t:"Diabetes"},
    {v:"ninguna",t:"Ninguna de las anteriores"}
  ];
  var opcionesZona = '<option value="">— Usar mi ubicación actual automáticamente —</option>';
  if($("sel-zona")){
    var sel = $("sel-zona");
    for(var i=0; i<sel.children.length; i++){
      var ch = sel.children[i];
      if(ch.tagName === "OPTGROUP"){
        opcionesZona += '<optgroup label="'+esc(ch.label)+'">';
        for(var j=0; j<ch.children.length; j++){
          var op = ch.children[j];
          opcionesZona += '<option value="'+esc(op.value)+'" '+(op.value===(sel.value||"")?'selected':'')+'>'+esc(op.text)+'</option>';
        }
        opcionesZona += '</optgroup>';
      } else if(ch.tagName === "OPTION" && ch.value){
        opcionesZona += '<option value="'+esc(ch.value)+'" '+(ch.value===(sel.value||"")?'selected':'')+'>'+esc(ch.text)+'</option>';
      }
    }
  }
  cont.innerHTML =
    '<p class="hoja__sub">Completa tus datos para crear tu cuenta personal de usuaria (gestante) y recibir alertas según tu embarazo y ubicación.</p>'+
    '<div class="perfil-sec"><h3>Cuenta de acceso</h3>'+
      '<div class="campo"><label>Nombre de usuario *</label><input id="reg-user" type="text" autocomplete="username" value="'+esc(userInicial||"")+'" placeholder="Ej. maria_g"></div>'+
      '<div class="campo"><label>Contraseña (mínimo 4 caracteres) *</label><input id="reg-pass" type="password" autocomplete="new-password" value="'+esc(passInicial||"")+'" placeholder="Escribe una contraseña segura"></div>'+
    '</div>'+
    '<div class="perfil-sec"><h3>Datos básicos</h3>'+
      '<div class="campo"><label>Nombre o Pseudónimo</label><input id="reg-nombre" type="text" placeholder="Cómo quieres que te llamemos"></div>'+
      '<div class="campo"><label>Edad</label><input id="reg-edad" type="number" min="12" max="60" placeholder="Ej. 26"></div>'+
      '<div class="campo"><label>Zona (UPZ) o ubicación</label><select id="reg-zona">'+opcionesZona+'</select></div>'+
    '</div>'+
    '<div class="perfil-sec"><h3>Tu embarazo</h3>'+
      '<div class="campo"><label>Fecha última menstruación (FUM)</label><input id="reg-fum" type="date"></div>'+
      '<div class="campo"><label>o Fecha probable de parto (FPP)</label><input id="reg-fpp" type="date"></div>'+
      '<label class="check"><input type="checkbox" id="reg-primero"> ¿Es tu primer embarazo?</label>'+
    '</div>'+
    '<div class="perfil-sec"><h3>Condiciones de salud</h3>'+
      '<div class="checks">'+condOpts.map(function(o){
        return '<label class="check"><input type="checkbox" class="reg-cond" value="'+o.v+'"> '+o.t+'</label>';
      }).join("")+'</div>'+
    '</div>'+
    '<div class="perfil-sec"><h3>Contacto de emergencia</h3>'+
      '<div class="campo"><label>Nombre del contacto</label><input id="reg-contacto-nombre" type="text" placeholder="Papá, mamá, pareja…"></div>'+
      '<div class="campo"><label>Teléfono del contacto</label><input id="reg-contacto-tel" type="tel" placeholder="Ej. 3001234567"></div>'+
    '</div>'+
    '<div class="perfil-sec"><h3>Preferencias de alertas</h3>'+
      '<div class="campo"><label>Hora preferida para reporte diario</label><input id="reg-hora" type="time" value="08:00"></div>'+
      '<label class="check"><input type="checkbox" id="reg-alertas" checked> Permitir alertas de emergencia en tiempo real</label>'+
      '<div class="campo" style="margin-top:12px"><label>Nota o comentario adicional</label><textarea id="reg-comentario" placeholder="Opcional"></textarea></div>'+
    '</div>'+
    '<p id="reg-msg" class="msg-err"></p>'+
    '<div class="acciones">'+
      '<button class="btn-prim" id="btn-crear-cuenta" type="button">Crear mi cuenta</button>'+
      '<button class="btn-sec" id="btn-volver-login" type="button">Ya tengo cuenta</button>'+
    '</div>';
  var h=$("hoja-perfil"); if(h) h.scrollTop=0;
  $("btn-volver-login").onclick = function(){ renderLogin(cont); };
  $("btn-crear-cuenta").onclick = async function(){
    var uVal = ($("reg-user").value||"").trim();
    var pVal = $("reg-pass").value||"";
    if(!uVal || pVal.length < 4){
      $("reg-msg").textContent = "Por favor escribe un nombre de usuario y una contraseña de al menos 4 caracteres.";
      return;
    }
    if(uVal.toLowerCase()==="admin"){
      $("reg-msg").textContent = "No es posible registrar cuentas de administrador. Elige otro nombre de usuario.";
      return;
    }
    var cs = Array.prototype.slice.call(document.querySelectorAll(".reg-cond:checked")).map(function(x){return x.value;});
    var zonaElegida = $("reg-zona") ? $("reg-zona").value : "";
    var profile = {
      nombre: ($("reg-nombre").value||"").trim(),
      edad: $("reg-edad").value||"",
      fum: $("reg-fum").value||"",
      fpp: $("reg-fpp").value||"",
      primer_embarazo: $("reg-primero").checked,
      condiciones: cs,
      contacto_emergencia_nombre: ($("reg-contacto-nombre").value||"").trim(),
      contacto_emergencia_telefono: ($("reg-contacto-tel").value||"").trim(),
      hora_reporte: $("reg-hora").value||"08:00",
      alertas_emergencia: $("reg-alertas").checked,
      comentario: ($("reg-comentario").value||"").trim()
    };
    if(zonaElegida) profile.zona = zonaElegida;
    if(ubicActual){
      profile.lat = ubicActual.lat;
      profile.lon = ubicActual.lon;
    }
    try{
      var d = await api("/api/auth/register",{
        method:"POST",
        body:JSON.stringify({username:uVal, password:pVal, role:"gestante", profile:profile})
      });
      guardarSesion(d.token, d.user);
      cerrarModal("hoja-perfil","modal-perfil");
      iniciarNotificaciones();
      if(ubicActual) enviarUbicacionServidor(ubicActual.lat, ubicActual.lon, true);
      if(zonaElegida && $("sel-zona")){
        $("sel-zona").value = zonaElegida;
        location.hash = "zona=" + zonaElegida;
        verZona(zonaElegida);
      } else {
        actualizarEstacionCercanaEnInicio();
        actualizarRecoPersonalizada();
      }
      cambiarVista("inicio");
      renderPerfil();
    }catch(e){
      $("reg-msg").textContent = mensajeErrorNatural(e);
    }
  };
}

function renderPerfilGestante(cont){
  var p = USUARIO.profile || {};
  var conds = p.condiciones || [];
  var condOpts = [
    {v:"asma",t:"Asma o problemas respiratorios"},
    {v:"hipertension",t:"Hipertensión o antecedentes de preeclampsia"},
    {v:"diabetes",t:"Diabetes"},
    {v:"ninguna",t:"Ninguna de las anteriores"}
  ];
  cont.innerHTML =
    '<p class="hoja__sub">Cuenta: <b>'+esc(USUARIO.username)+'</b> · rol gestante</p>'+
    '<div class="perfil-sec"><h3>Datos básicos</h3>'+
      '<div class="campo"><label>Nombre / Pseudónimo</label><input id="pf-nombre" value="'+esc(p.nombre||"")+'"></div>'+
      '<div class="campo"><label>Edad</label><input id="pf-edad" type="number" value="'+esc(p.edad||"")+'"></div>'+
    '</div>'+
    '<div class="perfil-sec"><h3>Tu embarazo</h3>'+
      '<div class="campo"><label>Fecha última menstruación (FUM)</label><input id="pf-fum" type="date" value="'+esc(p.fum||"")+'"></div>'+
      '<div class="campo"><label>o Fecha probable de parto (FPP)</label><input id="pf-fpp" type="date" value="'+esc(p.fpp||"")+'"></div>'+
      '<label class="check"><input type="checkbox" id="pf-primero" '+(p.primer_embarazo?'checked':'')+'> ¿Es tu primer embarazo?</label>'+
    '</div>'+
    '<div class="perfil-sec"><h3>Salud</h3>'+
      '<div class="checks">'+condOpts.map(function(o){
        return '<label class="check"><input type="checkbox" class="pf-cond" value="'+o.v+'" '+(conds.indexOf(o.v)>=0?'checked':'')+'> '+o.t+'</label>';
      }).join("")+'</div>'+
    '</div>'+
    '<div class="perfil-sec"><h3>Contacto de emergencia</h3>'+
      '<div class="campo"><label>Nombre</label><input id="pf-contacto-nombre" value="'+esc(p.contacto_emergencia_nombre||"")+'" placeholder="Papá, mamá, pareja…"></div>'+
      '<div class="campo"><label>Teléfono</label><input id="pf-contacto-tel" type="tel" value="'+esc(p.contacto_emergencia_telefono||"")+'" placeholder="Ej. 3001234567"></div>'+
      '<p class="ubic-aviso">Se guarda en tu perfil para avisarle rápido en una emergencia, sin tener que buscarlo.</p>'+
    '</div>'+
    '<div class="perfil-sec"><h3>Alertas</h3>'+
      '<div class="campo"><label>Hora preferida para reportes diarios</label><input id="pf-hora" type="time" value="'+esc(p.hora_reporte||"08:00")+'"></div>'+
      '<label class="check"><input type="checkbox" id="pf-alertas" '+(p.alertas_emergencia!==false?'checked':'')+'> Permitir alertas de emergencia en tiempo real</label>'+
      '<div class="campo" style="margin-top:12px"><label>Comentario / sugerencia para tus avisos</label><textarea id="pf-comentario">'+esc(p.comentario||"")+'</textarea></div>'+
    '</div>'+
    '<div class="acciones">'+
      '<button class="btn-prim" id="btn-guardar-perfil">Guardar</button>'+
      '<button class="btn-sec" id="btn-notif-permiso">Activar notificaciones</button>'+
      '<button class="btn-sec" id="btn-logout">Cerrar sesión</button></div>'+
    '<div id="pf-msg" style="margin-top:8px"></div>';
  $("btn-guardar-perfil").onclick = async function(){
    var cs = Array.prototype.slice.call(document.querySelectorAll(".pf-cond:checked")).map(function(x){return x.value;});
    var profile = {
      nombre: $("pf-nombre").value, edad: $("pf-edad").value,
      fum: $("pf-fum").value, fpp: $("pf-fpp").value,
      primer_embarazo: $("pf-primero").checked, condiciones: cs,
      contacto_emergencia_nombre: $("pf-contacto-nombre").value,
      contacto_emergencia_telefono: $("pf-contacto-tel").value,
      hora_reporte: $("pf-hora").value,
      alertas_emergencia: $("pf-alertas").checked,
      comentario: $("pf-comentario").value
    };
    if($("sel-zona") && $("sel-zona").value) profile.zona = $("sel-zona").value;
    try{
      await api("/api/users/me/profile",{method:"PUT",body:JSON.stringify({profile:profile})});
      USUARIO.profile = Object.assign({}, USUARIO.profile, profile);
      guardarSesion(TOKEN, USUARIO);
      $("pf-msg").textContent = "Perfil guardado."; $("pf-msg").className = "msg-ok";
      actualizarRecoPersonalizada();
    }catch(e){ $("pf-msg").textContent="Error: "+e.message; $("pf-msg").className="msg-err"; }
  };
  if($("btn-notif-permiso")) $("btn-notif-permiso").onclick = pedirPermisoNotificaciones;
  $("btn-logout").onclick = async function(){
    try{ await api("/api/auth/logout",{method:"POST"}); }catch(e){}
    guardarSesion("",""); renderPerfil();
  };
  actualizarRecoPersonalizada();
}

function renderAdmin(cont){
  cont.innerHTML =
    '<p class="hoja__sub">Solo variables globales para <b>probar alertas</b>. El administrador no tiene perfil de gestante.</p>'+
    '<div class="perfil-sec"><h3>Datos de la API</h3>'+
      '<div class="acciones"><button class="btn-prim" id="adm-refresh">Actualizar datos API</button></div>'+
      '<div id="adm-refresh-status" style="margin-top:8px;font-size:.86rem"></div>'+
    '</div>'+
    '<div class="perfil-sec"><h3>Modo prueba (global)</h3>'+
      '<div class="campo"><label>Factor global (multiplica ICA y sub-índices)</label><input id="adm-factor" type="number" step="0.1" value="1.5"></div>'+
      '<div class="campo"><label>Forzar nivel global</label><select id="adm-nivel"><option value="">(sin forzar)</option><option value="verde">verde</option><option value="naranja">naranja</option><option value="rojo">rojo</option></select></div>'+
      '<div class="acciones"><button class="btn-prim" id="adm-test-on">Activar modo prueba</button><button class="btn-sec" id="adm-test-off">Volver a normal</button></div>'+
      '<div id="adm-status" style="margin-top:8px;font-size:.9rem"></div>'+
    '</div>'+
    '<div class="perfil-sec"><h3>Por estación (opcional)</h3>'+
      '<div class="campo"><label>Estación</label><select id="adm-est"><option value="">— Cargando… —</option></select></div>'+
      '<div class="campo"><label>Factor de la estación</label><input id="adm-est-factor" type="number" step="0.1" value="2"></div>'+
      '<div class="campo"><label>Nivel de la estación</label><select id="adm-est-nivel"><option value="">(sin forzar)</option><option value="verde">verde</option><option value="naranja">naranja</option><option value="rojo">rojo</option></select></div>'+
      '<div class="acciones"><button class="btn-sec" id="adm-est-aplicar">Aplicar a la estación</button></div>'+
      '<div id="adm-est-status" style="margin-top:8px;font-size:.86rem"></div>'+
    '</div>'+
    '<div class="acciones"><button class="btn-sec" id="btn-logout-admin">Cerrar sesión</button></div>';

  $("adm-refresh").onclick = async function(){
    try{ await api("/api/admin/refresh",{method:"POST"}); $("adm-refresh-status").textContent="Actualización de la API iniciada…"; }catch(e){ $("adm-refresh-status").textContent="Error: "+e.message; }
  };
  $("adm-test-on").onclick = async function(){
    var factor = parseFloat($("adm-factor").value) || 1.0;
    var nivel = $("adm-nivel").value || null;
    var g = {factor:factor}; if(nivel) g.nivel = nivel;
    try{
      var d = await api("/api/admin/test-mode",{method:"POST",body:JSON.stringify({enabled:true,global:g})});
      $("adm-status").textContent="Modo prueba activado. Alertas generadas: "+(d.alertas_generadas||0);
      var c=$("prueba-cinta"); if(c) c.hidden=false;
      sincronizarDatosServidor(true);
    }catch(e){ $("adm-status").textContent="Error: "+e.message; }
  };
  $("adm-test-off").onclick = async function(){
    try{
      await api("/api/admin/test-mode",{method:"POST",body:JSON.stringify({enabled:false})});
      $("adm-status").textContent="Modo normal restaurado.";
      var c=$("prueba-cinta"); if(c) c.hidden=true;
      sincronizarDatosServidor(true);
    }catch(e){ $("adm-status").textContent="Error: "+e.message; }
  };
  $("adm-est-aplicar").onclick = async function(){
    var uid = $("adm-est").value;
    if(!uid){ $("adm-est-status").textContent="Elige una estación."; return; }
    var factor = parseFloat($("adm-est-factor").value) || 1.0;
    var nivel = $("adm-est-nivel").value || null;
    var cfg = {factor:factor}; if(nivel) cfg.nivel = nivel;
    var estaciones = {}; estaciones[uid] = cfg;
    try{
      var d = await api("/api/admin/test-mode",{method:"POST",body:JSON.stringify({enabled:true,estaciones:estaciones})});
      $("adm-est-status").textContent="Aplicado a la estación. Alertas generadas: "+(d.alertas_generadas||0);
      var c=$("prueba-cinta"); if(c) c.hidden=false;
      sincronizarDatosServidor(true);
    }catch(e){ $("adm-est-status").textContent="Error: "+e.message; }
  };
  $("btn-logout-admin").onclick = async function(){
    try{ await api("/api/auth/logout",{method:"POST"}); }catch(e){}
    guardarSesion("",""); renderPerfil();
  };
  fetch("/api/waqi/estaciones").then(function(r){return r.ok?r.json():null;}).then(function(d){
    var sel=$("adm-est"); if(!sel) return;
    sel.innerHTML='<option value="">— Elige una estación —</option>';
    ((d&&d.estaciones)||[]).forEach(function(e){ sel.add(new Option(e.nombre||e.uid, e.uid)); });
  }).catch(function(){});
}

/* Recomendación personalizada: la calcula el SERVIDOR según el perfil. */
function actualizarRecoPersonalizada(){
  if(!TOKEN || !USUARIO || !$("reco-txt")){ return; }
  api("/api/perfil/recomendacion").then(function(rec){
    if(rec) pintarRecoPersonalizada(rec);
  }).catch(function(){});
}

/* ============ Indicaciones (vista completa) ============ */
function simboloNivel(n){
  return n==="verde" ? "✓" : (n==="rojo" ? "!" : (n==="naranja" ? "!" : "–"));
}
function pintarIndHero(nivel, titulo, sub){
  var n=nivelClase(nivel);
  var h=$("ind-hero"); if(!h) return;
  h.className="ind-hero nivel-"+n;
  $("ind-ico").textContent = simboloNivel(n);
  $("ind-titulo").textContent = titulo;
  $("ind-sub").textContent = sub;
}
var ICONO_SEC = {aire:"🌫️", embarazo:"🤰", salud:"❤️", contaminantes:"🧪", accion:"✅", nota:"📝"};
function verIndicaciones(){
  var cont=$("ind-cont");
  if(!TOKEN || !USUARIO){
    pintarIndHero("gris","Indicaciones","Inicia sesión para personalizarlas.");
    cont.innerHTML='<div class="aviso-estado">Inicia sesión para ver indicaciones según tu embarazo, tus condiciones y tu zona. Mientras tanto puedes revisar la <b>Guía</b> y el <b>Mapa</b>.</div>';
    return;
  }
  cont.innerHTML='<div class="cargando">Cargando tus indicaciones…</div>';
  api("/api/perfil/indicaciones").then(function(d){
    var n=nivelClase(d.nivel);
    pintarIndHero(n, d.titular||"Indicaciones",
      d.estacion&&d.estacion.nombre ? ("Estación de referencia: "+d.estacion.nombre) : "Según tu perfil");
    aplicarTema(n);
    var color=d.color||colorNivel(n);
    // Tabla de resumen (fácil de leer de un vistazo).
    var filas = '<tr><th>Nivel de aire</th><td><span class="pt" style="background:'+color+'"></span>'+esc(ETIQUETA_NIVEL[n])+'</td></tr>';
    if(d.estacion&&d.estacion.nombre) filas += '<tr><th>Estación</th><td>'+esc(d.estacion.nombre)+'</td></tr>';
    if(d.semanas!=null) filas += '<tr><th>Tu embarazo</th><td>Semana '+esc(d.semanas)+' · trimestre '+esc(d.trimestre)+'</td></tr>';
    var html = '<div class="ind-sec"><h3>📊 Resumen</h3><table class="tabla">'+filas+'</table></div>';
    var secs=(d.secciones||[]).filter(function(s){ return s.items && s.items.length; });
    html += secs.map(function(s){
      var ico = ICONO_SEC[s.icono] || "•";
      var cuerpo;
      if(s.icono==="accion"){
        cuerpo = '<table class="tabla">'+s.items.map(function(it){
          return '<tr><td style="width:22px;color:'+color+';font-weight:900">✓</td><td>'+esc(it)+'</td></tr>';
        }).join("")+'</table>';
      } else {
        cuerpo = '<ul>'+s.items.map(function(it){return '<li>'+esc(it)+'</li>';}).join("")+'</ul>';
      }
      return '<div class="ind-sec"><h3>'+ico+' '+esc(s.titulo)+'</h3>'+cuerpo+'</div>';
    }).join("");
    cont.innerHTML = html;
  }).catch(function(){
    pintarIndHero("gris","Indicaciones","No disponible ahora.");
    cont.innerHTML='<div class="aviso-estado">No se pudieron cargar tus indicaciones ahora.</div>';
  });
}

/* ============ Notificaciones ============ */
function pedirPermisoNotificaciones(){
  try{
    if(window.AndroidBridge && AndroidBridge.pedirNotificaciones){ AndroidBridge.pedirNotificaciones(); return; }
    if("Notification" in window){ Notification.requestPermission(); }
  }catch(e){}
}
function mostrarNotificacionSistema(n){
  try{
    if(window.AndroidBridge && AndroidBridge.notificar){
      AndroidBridge.notificar(n.titulo||"MobiAire", n.mensaje||"", n.tipo||"");
      return;
    }
    if("Notification" in window && Notification.permission==="granted"){
      var tag = (n.tipo==="alerta" || (n.titulo||"").indexOf("Alerta")>=0) ? "mobiaire-semaforo" : "mobiaire-sistema";
      var noti = new Notification(n.titulo||"MobiAire Materno", {body:n.mensaje||"", tag:tag});
      noti.onclick = function(){
        try{ window.focus(); }catch(e){}
        if(n.tipo==="reporte_diario") cambiarVista("indicaciones");
        noti.close();
      };
    }
  }catch(e){}
}
function cargarNotificaciones(){
  if(!TOKEN) return;
  fetch("/api/notifications?since="+NOTIF_LAST, {headers:authHeaders()}).then(function(r){
    if(r.status===401){ cerrarSesionRemota(); return null; }
    return r.ok?r.json():null;
  }).then(function(d){
    if(!d) return;
    var lista=d.notificaciones||[];
    lista.forEach(function(n){
      NOTIF_LAST = Math.max(NOTIF_LAST, n.id);
      if(!NOTIF_VISTAS[n.id]){ NOTIF_VISTAS[n.id]=true; mostrarNotificacionSistema(n); }
    });
    var badge=$("notif-badge");
    if(badge){ badge.hidden = lista.length===0; badge.textContent = lista.length; }
    var cont=$("notif-cont");
    if(cont && !cont.dataset.cargado){ pintarNotif(cont, lista); }
  }).catch(function(){});
}
function renderNotif(n){
  var nv=nivelClase(n.nivel);
  var color=colorNivel(nv);
  return '<div class="alerta nivel-'+nv+'">'+
    '<span class="alerta__ico" style="background:'+color+'">'+simboloNivel(nv)+'</span>'+
    '<div class="alerta__txt"><b>'+esc(n.titulo||"Aviso")+'</b><p>'+esc(n.mensaje||"")+'</p>'+
    '<p style="color:var(--tinta-suave);font-size:.74rem;margin-top:4px">'+esc(haceCuanto(n.creada_en)||n.creada_en||"")+'</p></div>'+
    '<button class="btn-sec btn-min alerta__btn" data-ind="1" type="button">Ver indicaciones</button></div>';
}
function pintarNotif(cont, lista){
  if(!lista.length){ cont.innerHTML='<div class="aviso-estado">No tienes alertas.</div>'; return; }
  cont.innerHTML = lista.map(renderNotif).join("");
  cont.querySelectorAll("[data-ind]").forEach(function(b){
    b.onclick=function(){ cerrarModal("hoja-notif","modal-notif"); cambiarVista("indicaciones"); };
  });
}
function abrirNotificaciones(){
  $("notif-cont").dataset.cargado="";
  abrirModal("hoja-notif","modal-notif");
  if(!TOKEN){
    $("notif-cont").innerHTML='<div class="aviso-estado">Inicia sesión para recibir alertas personalizadas.</div>';
    return;
  }
  fetch("/api/notifications",{headers:authHeaders()}).then(function(r){return r.ok?r.json():null;}).then(function(d){
    pintarNotif($("notif-cont"), (d&&d.notificaciones)||[]);
    api("/api/notifications/leer",{method:"POST",body:JSON.stringify({})}).catch(function(){});
    var badge=$("notif-badge"); if(badge) badge.hidden=true;
  }).catch(function(){});
}
function iniciarNotificaciones(){
  if(NOTIF_TIMER) clearInterval(NOTIF_TIMER);
  sincronizarServicioNativo();
  cargarNotificaciones();
  NOTIF_TIMER = setInterval(cargarNotificaciones, 12000);
}

/* ============ Emergencia (SOS) ============ */
function abrirSOS(){
  var cont = $("sos-cont");
  cont.innerHTML =
    '<p class="sos-tit">Opciones de emergencia</p>'+
    '<div style="display:flex;flex-direction:column;gap:8px">'+
    '<button class="btn-prim" id="sos-hosp">Hospitales/centros de salud cercanos</button>'+
    '<button class="btn-prim" id="sos-contacto">Avisar a contacto cercano (SMS/llamada)</button>'+
    '<button class="btn-sec" id="sos-123">Llamar a emergencias (123)</button></div>'+
    '<div id="sos-lista" style="margin-top:12px"></div>';
  $("sos-hosp").onclick = buscarHospitales;
  $("sos-contacto").onclick = avisarContacto;
  $("sos-123").onclick = function(){ llamar("123"); };
  abrirModal("hoja-sos","modal-sos");
}
function llamar(numero){
  try{
    if(window.AndroidBridge && AndroidBridge.llamar){ AndroidBridge.llamar(String(numero)); return; }
  }catch(e){}
  window.location.href="tel:"+numero;
}
function enviarSMS(numero, mensaje){
  try{
    if(window.AndroidBridge && AndroidBridge.enviarSMS){ AndroidBridge.enviarSMS(String(numero), String(mensaje)); return true; }
  }catch(e){}
  try{ window.location.href="sms:"+numero+"?body="+encodeURIComponent(mensaje); return true; }catch(e){}
  return false;
}
function compartirTexto(titulo, texto){
  try{
    var ab=bridge();
    if(ab && ab.compartir){ ab.compartir(String(titulo||"MobiAire"), String(texto)); return; }
  }catch(e){}
  if(navigator.share){ try{ navigator.share({title:titulo||"MobiAire", text:texto}).catch(function(){}); return; }catch(e){} }
  try{ window.location.href="sms:?body="+encodeURIComponent(texto); }catch(e){}
}
/* Lista de centros de salud: el SERVIDOR consulta y sirve desde su base de datos. */
function buscarHospitales(){
  var lista = $("sos-lista");
  lista.innerHTML = '<div class="cargando">Consultando centros de salud cercanos en el servidor…</div>';

  function pedirAlServidor(la, lo, esRespaldo){
    if(!esRespaldo) enviarUbicacionServidor(la, lo);
    return fetch("/api/salud/cercanos?lat="+la+"&lon="+lo+"&radio=8000").then(function(r){
      if(!r.ok) throw new Error("salud "+r.status);
      return r.json();
    }).then(function(d){
      var lugares=(d.lugares||[]);
      if(!lugares.length){ lista.innerHTML='<div class="aviso-estado">No se encontraron centros de salud cercanos.</div>'; return; }
      lista.innerHTML = '<p class="sos-tit">Centros cercanos ('+lugares.length+') · toca para ver el detalle</p>'+
        (esRespaldo ? '<p class="ubic-aviso" style="margin-bottom:8px">Mostrando los centros más cercanos a tu zona de referencia.</p>' : '')+
        lugares.map(function(p,i){
          var sub = (p.distancia_km!=null? p.distancia_km+" km" : "") + (p.telefono? " · "+p.telefono : " · sin teléfono");
          return '<button class="toca" type="button" data-hosp="'+i+'">'+
            '<span class="toca__ico">🏥</span><span class="toca__txt"><b>'+esc(p.nombre)+'</b><span>'+esc(sub)+'</span></span>'+
            '<span class="toca__flecha">›</span></button>';
        }).join("")+
        '<p class="ubic-aviso">Fuente: '+esc(d.fuente||"Base de datos del servidor")+'. '+
          (d.desde_cache?"Servido al instante desde la base de datos del servidor.":"Datos actualizados y guardados en el servidor.")+'</p>';
      lista.querySelectorAll("[data-hosp]").forEach(function(b){
        b.onclick=function(){ abrirDetalleHospital(lugares[+b.dataset.hosp], {lat:la,lon:lo}); };
      });
    }).catch(function(){
      lista.innerHTML='<div class="aviso-estado">No se pudo conectar con el servidor para consultar los centros de salud.</div>';
    });
  }

  if(ubicActual && ubicActual.lat!=null && ubicActual.lon!=null){
    pedirAlServidor(ubicActual.lat, ubicActual.lon, false);
    return;
  }

  getUbicacionActual().then(function(pos){
    pedirAlServidor(pos.lat, pos.lon, false);
  }).catch(function(){
    // Respaldo: coordenadas guardadas en el perfil, estación más cercana o centro de Ciudad Bolívar / Sur
    var p = (USUARIO && USUARIO.profile) || {};
    if(p.lat!=null && p.lon!=null){
      pedirAlServidor(parseFloat(p.lat), parseFloat(p.lon), true);
      return;
    }
    var refLat = 4.5721, refLon = -74.1453;
    if(DATOS && DATOS.length && DATOS[0].lat!=null){
      var cb = DATOS.find(function(x){ return String(x.nombre||"").toLowerCase().indexOf("bol")>=0; }) || DATOS[0];
      refLat = cb.lat; refLon = cb.lon;
    }
    pedirAlServidor(refLat, refLon, true);
  });
}
/* Sub-interfaz con el detalle de un centro: distancia, mapa, cómo llegar, llamar. */
function abrirDetalleHospital(p, mi){
  var tel = p.telefono||"";
  var dist = (p.distancia_km!=null)? (p.distancia_km+" km") : "—";
  var html =
    '<span class="hosp-tipo">'+esc(p.tipo||"salud")+'</span>'+
    '<p class="hoja__sub" style="margin-top:8px">'+esc(p.descripcion||"Centro de salud cercano a tu ubicación.")+'</p>'+
    '<div class="hosp-mapa" id="hosp-mapa"></div>'+
    '<div class="hosp-dato"><b>Distancia:</b><span>'+esc(dist)+' desde tu ubicación</span></div>'+
    (p.direccion?('<div class="hosp-dato"><b>Dirección:</b><span>'+esc(p.direccion)+'</span></div>'):'')+
    (p.operador?('<div class="hosp-dato"><b>Operador:</b><span>'+esc(p.operador)+'</span></div>'):'')+
    '<div class="hosp-dato"><b>Teléfono:</b><span>'+(tel?esc(tel):"No reportado")+'</span></div>'+
    '<div class="acciones" style="margin-top:6px">'+
      '<button class="btn-prim" id="hosp-ir" type="button">Cómo llegar</button>'+
      '<button class="btn-sec" id="hosp-compartir" type="button">Compartir ubicación</button>'+
      (tel?('<button class="btn-sec" id="hosp-llamar" type="button">Llamar</button>'):'')+
    '</div>';
  abrirDetalle(p.nombre||"Centro de salud", html);
  setTimeout(function(){
    try{
      if(!window.L) return;
      var el=$("hosp-mapa"); if(!el) return;
      var m=L.map(el,{zoomControl:false,attributionControl:false}).setView([p.lat,p.lon],15);
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",{maxZoom:18,attribution:'© OpenStreetMap'}).addTo(m);
      L.marker([p.lat,p.lon]).addTo(m).bindPopup(esc(p.nombre||"Centro")).openPopup();
      if(mi && mi.lat!=null){
        L.circleMarker([mi.lat,mi.lon],{radius:7,color:"#fff",weight:2,fillColor:"#2f6b4f",fillOpacity:1})
          .addTo(m).bindPopup("Tu ubicación");
      }
      m.invalidateSize();
    }catch(e){}
  },140);
  $("hosp-ir").onclick=function(){
    var ab = bridge();
    if(ab && ab.abrirMapa){
      try{ ab.abrirMapa(p.lat, p.lon, p.nombre||"Centro de salud"); return; }catch(e){}
    }
    window.open("https://www.google.com/maps/dir/?api=1&destination="+p.lat+","+p.lon,"_blank");
  };
  $("hosp-compartir").onclick=function(){
    compartirTexto(p.nombre||"Centro de salud",
      (p.nombre||"Centro de salud")+" — ubicación: https://maps.google.com/?q="+p.lat+","+p.lon);
  };
  if(tel && $("hosp-llamar")) $("hosp-llamar").onclick=function(){ llamar(tel); };
}
async function avisarContacto(){
  var lista = $("sos-lista");
  lista.innerHTML = '<div class="cargando">Preparando el aviso…</div>';
  var perfil = (USUARIO && USUARIO.profile) || {};
  var elegido = null;
  // 1) Contacto guardado en el perfil (sin buscar durante la emergencia).
  if(perfil.contacto_emergencia_telefono){
    elegido = {name: perfil.contacto_emergencia_nombre||"Contacto de emergencia",
               phone: perfil.contacto_emergencia_telefono};
  }
  // 2) Contactos guardados antiguos.
  if(!elegido){
    var contactos = perfil.contactos_emergencia || [];
    if(contactos.length) elegido = contactos[0];
  }
  // 3) Selector del teléfono o entrada manual.
  if(!elegido && "contacts" in navigator && "ContactsManager" in window){
    try{
      var sel = await navigator.contacts.select(["name","tel"], {multiple:false});
      if(sel && sel.length){ elegido = {name:sel[0].name?sel[0].name[0]:"Contacto", phone:sel[0].tel?sel[0].tel[0]:""}; }
    }catch(e){}
  }
  if(!elegido) elegido = await elegirContactoNativo();
  if(!elegido){
    var nombre = prompt("Nombre del contacto (papá, mamá, pareja…):");
    var tel = prompt("Teléfono del contacto:");
    if(!tel){ lista.innerHTML='<div class="aviso-estado">No se seleccionó contacto.</div>'; return; }
    elegido = {name:nombre||"Contacto", phone:tel};
  }
  getUbicacionActual().then(function(pos){
    var la=pos.lat, lo=pos.lon;
    var msg = "Estoy sintiéndome mal, por favor ven de inmediato a mi ubicación: https://maps.google.com/?q="+la+","+lo;
    lista.innerHTML = '<p class="sos-tit">Avisar a tu contacto</p>'+
      '<p>Contacto: <b>'+esc(elegido.name)+'</b> ('+esc(elegido.phone)+')</p>'+
      '<div class="acciones">'+
      '<button class="btn-prim" id="sos-llamar-contacto" type="button">Llamar</button>'+
      '<button class="btn-sec" id="sos-sms-contacto" type="button">Enviar SMS con ubicación</button></div>'+
      '<p class="ubic-aviso" id="sos-sms-aviso">Se abrirá tu app de mensajes con el texto y la ubicación listos para enviar.</p>';
    $("sos-llamar-contacto").onclick = function(){ llamar(elegido.phone); };
    $("sos-sms-contacto").onclick = function(){
      var aviso=$("sos-sms-aviso");
      var ok = enviarSMS(elegido.phone, msg);
      if(aviso) aviso.textContent = ok
        ? "Abriendo tu app de mensajes con la ubicación…"
        : "No se pudo abrir la app de mensajes en este dispositivo.";
    };
  }).catch(function(){ lista.innerHTML='<div class="aviso-estado">Permiso de ubicación denegado; no se puede enviar la ubicación.</div>'; });
}

/* ============ Mapa: ubicación y zonas ============ */
function dibujarZonasMapa(){
  if(!mapa || !window.L) return;
  zonaLayers.forEach(function(l){ mapa.removeLayer(l); });
  zonaLayers = [];
  // 1) Contorno de las UPZ de Ciudad Bolívar (contexto, sin colorear).
  fetch("/api/upz-ciudad-bolivar").then(function(r){return r.ok?r.json():null;}).then(function(gj){
    if(!gj || !gj.features || !gj.features.length) return;
    var layer=L.geoJSON(gj,{style:function(){return {color:"#9aa7a0",weight:1,fillColor:"#9aa7a0",fillOpacity:0.05};},
      onEachFeature:function(f,l){ l.bindPopup(esc((f.properties&&f.properties.UPlNombre)||"UPZ")); }});
    layer.addTo(mapa); zonaLayers.push(layer);
  }).catch(function(){});
  // 2) Zonas alrededor de cada estación (radar), coloreadas por nivel.
  fetch("/api/zonas-mapa").then(function(r){return r.ok?r.json():null;}).then(function(d){
    if(!d) return;
    var radio=(d.radio_km||1.5)*1000;
    (d.zonas||[]).forEach(function(z){
      if(z.lat==null||z.lon==null) return;
      var n=nivelClase(z.nivel), color=colorNivel(n);
      var c=L.circle([z.lat,z.lon],{radius:radio,color:color,weight:2,fillColor:color,fillOpacity:0.28});
      var sem=ETIQUETA_NIVEL[n]||"Sin dato";
      c.bindPopup('<b>'+esc(z.nombre)+'</b><br>'+esc(sem)+(z.ica!=null?(' · ICA est. '+esc(z.ica)):'')+(z.reciente?'':'<br><em>Sin dato reciente</em>'));
      c.on("click",function(){ seleccionarEst(z.uid); });
      c.addTo(mapa); zonaLayers.push(c);
    });
  }).catch(function(){});
}

/* ============ Info y glosario ============ */
function abrirDetalle(titulo, html){
  $("detalle-tit").textContent = titulo;
  $("detalle-cont").innerHTML = html;
  abrirModal("hoja-detalle","modal-detalle");
  var h=$("hoja-detalle"); if(h) h.scrollTop=0;
}
function cerrarDetalle(){ cerrarModal("hoja-detalle","modal-detalle"); }
function abrirDetalleSemana(s){
  var html = '<p class="hoja__sub">'+esc(s.resumen)+'</p>'+
    '<div class="reco__bloque"><b>Riesgos en esta etapa</b><ul class="reco__lista">'+
      (s.riesgos||[]).map(function(t){return '<li>'+esc(t)+'</li>';}).join("")+'</ul></div>'+
    '<div class="reco__bloque"><b>Contaminantes clave</b>'+esc(s.pm||"")+'</div>'+
    '<div class="reco__bloque"><b>Qué hacer</b><ul class="reco__lista">'+
      (s.recomendaciones||[]).map(function(t){return '<li>'+esc(t)+'</li>';}).join("")+'</ul></div>';
  abrirDetalle(s.rango+" · "+s.titulo, html);
}
function abrirDetalleGlosario(x){
  var html = '<p class="hoja__sub">'+esc(x.definicion)+'</p>';
  if(x.causas&&x.causas.length)
    html += '<div class="reco__bloque"><b>Qué lo causa</b><ul class="reco__lista">'+
      x.causas.map(function(t){return '<li>'+esc(t)+'</li>';}).join("")+'</ul></div>';
  if(x.prevencion&&x.prevencion.length)
    html += '<div class="reco__bloque"><b>Cómo prevenirlo</b><ul class="reco__lista">'+
      x.prevencion.map(function(t){return '<li>'+esc(t)+'</li>';}).join("")+'</ul></div>';
  if(x.embarazo)
    html += '<div class="reco__bloque"><b>En el embarazo</b>'+esc(x.embarazo)+'</div>';
  abrirDetalle(x.termino, html);
}
function abrirDetalleContaminante(c){
  var html = '<p class="hoja__sub">'+esc(c.que_es)+'</p>'+
    '<table class="tabla">'+
    '<tr><th>Fuentes</th><td>'+esc(c.fuentes)+'</td></tr>'+
    '<tr><th>Efecto</th><td>'+esc(c.efecto)+'</td></tr>'+
    '<tr><th>Límite de referencia</th><td>'+esc(c.limite)+'</td></tr>'+
    '</table>';
  abrirDetalle(c.nombre, html);
}
function pintarInfo(){
  var semanas=(RECOS&&RECOS.semanas)||[];
  var cont=$("info-semanas");
  if(cont){
    cont.innerHTML = semanas.length ? semanas.map(function(s){
      return '<button class="toca" type="button" data-sem="'+esc(s.id)+'">'+
        '<span class="toca__ico">🤰</span><span class="toca__txt"><b>'+esc(s.rango)+'</b><span>'+esc(s.titulo)+'</span></span>'+
        '<span class="toca__flecha">›</span></button>';
    }).join("") : '<div class="aviso-estado">Sin información.</div>';
    cont.querySelectorAll("[data-sem]").forEach(function(b){
      b.onclick=function(){ var s=semanas.find(function(x){return x.id===b.dataset.sem;}); if(s) abrirDetalleSemana(s); };
    });
  }
  var glos=(RECOS&&RECOS.glosario)||[];
  var g=$("glosario-cont");
  if(g){
    g.innerHTML = glos.length ? glos.map(function(x){
      return '<button class="toca" type="button" data-glo="'+esc(x.id)+'">'+
        '<span class="toca__ico">📖</span><span class="toca__txt"><b>'+esc(x.termino)+'</b><span>'+esc(x.resumen||"")+'</span></span>'+
        '<span class="toca__flecha">›</span></button>';
    }).join("") : '<div class="aviso-estado">Sin glosario.</div>';
    g.querySelectorAll("[data-glo]").forEach(function(b){
      b.onclick=function(){ var x=glos.find(function(y){return y.id===b.dataset.glo;}); if(x) abrirDetalleGlosario(x); };
    });
  }
  var conts=(RECOS&&RECOS.contaminantes)||[];
  var c=$("contaminantes-cont");
  if(c){
    c.innerHTML = conts.length ?
      '<p class="hoja__sub">La app tiene en cuenta estos contaminantes cuando la estación los reporta. Toca uno para ver el detalle.</p>'+
      '<table class="tabla"><thead><tr><th>Contaminante</th><th>Efecto</th></tr></thead><tbody>'+
      conts.map(function(x){
        return '<tr class="tabla--fila" data-cont="'+esc(x.clave)+'"><td><b>'+esc(x.nombre)+'</b></td><td>'+esc(x.efecto)+'</td></tr>';
      }).join("")+'</tbody></table>'
      : '<div class="aviso-estado">Sin información.</div>';
    c.querySelectorAll("[data-cont]").forEach(function(tr){
      tr.onclick=function(){ var x=conts.find(function(y){return y.clave===tr.dataset.cont;}); if(x) abrirDetalleContaminante(x); };
    });
  }
  var acerca =
    '<h3>¿Qué es MobiAire Materno?</h3>'+
    '<p>Semáforo informativo de calidad del aire pensado para mujeres gestantes de Ciudad Bolívar, Bogotá. Traduce los datos de las estaciones de monitoreo en un color y en indicaciones de autocuidado.</p>'+
    '<h3>Fuentes de datos</h3>'+
    '<table class="tabla">'+
    '<tr><th>WAQI / AQICN</th><td>World Air Quality Index Project: entrega las mediciones recientes de las estaciones (índice en escala US-EPA).</td></tr>'+
    '<tr><th>OAB / RMCAB</th><td>Observatorio Ambiental de Bogotá y Red de Monitoreo de Calidad del Aire: red oficial de estaciones.</td></tr>'+
    '<tr><th>IDEAM / SISAIRE</th><td>Datos históricos (dataset <code>g4t8-zkc3</code>) usados para validar el motor del ICA.</td></tr>'+
    '<tr><th>OpenStreetMap / Overpass</th><td>Hospitales y centros de salud cercanos y las teselas del mapa.</td></tr>'+
    '</table>'+
    '<h3>¿Cómo se generan los mapas?</h3>'+
    '<p>El mapa se dibuja en el teléfono con la librería <b>Leaflet</b>:</p>'+
    '<ul class="reco__lista">'+
    '<li><b>Fondo:</b> teselas raster de OpenStreetMap (<code>tile.openstreetmap.org</code>).</li>'+
    '<li><b>Zonas:</b> círculos (radar) alrededor de cada estación, coloreados según el semáforo; gris cuando no hay dato reciente.</li>'+
    '<li><b>Contornos:</b> las UPZ de Ciudad Bolívar se dibujan con un GeoJSON que sirve el propio servidor.</li>'+
    '<li><b>Marcadores:</b> un punto por estación; al tocarlo muestra el semáforo y el índice.</li>'+
    '<li><b>Hospitales:</b> un mapa pequeño dentro del detalle del centro de salud.</li>'+
    '</ul>'+
    '<h3>Servicios y programas usados</h3>'+
    '<table class="tabla">'+
    '<tr><th>Servidor</th><td>Python (biblioteca estándar, <code>http.server</code>) como conmutador único hacia WAQI; guarda todo en <b>SQLite</b>.</td></tr>'+
    '<tr><th>App Android</th><td>Kotlin con WebView nativo, servicio en segundo plano y puente de ubicación/contactos/llamadas.</td></tr>'+
    '<tr><th>App iOS</th><td>Swift con WKWebView (mismo cliente web).</td></tr>'+
    '<tr><th>Cliente web</th><td>HTML, CSS y JavaScript sin frameworks (Leaflet solo para el mapa).</td></tr>'+
    '<tr><th>Iconografía</th><td>Logo vectorial propio generado con la biblioteca estándar de Python.</td></tr>'+
    '</table>'+
    '<h3>El proyecto</h3><p>Universidad Distrital Francisco José de Caldas — semáforo informativo, <b>no</b> un diagnóstico médico.</p>';
  if($("acerca-cont")) $("acerca-cont").innerHTML = acerca;
  if($("acerca-cont2")) $("acerca-cont2").innerHTML = acerca;
}

/* ============ Ubicación automática y tema ============ */
function inyectarEstilosTema(){
  if(document.getElementById("tema-style")) return;
  var st=document.createElement("style");
  st.id="tema-style";
  st.textContent=
    "body.tema-verde{background:linear-gradient(180deg,#e4f4ea,#faf8f3);transition:background 1s;}"+
    "body.tema-naranja{background:linear-gradient(180deg,#fbedd,#faf8f3);transition:background 1s;}"+
    "body.tema-rojo{background:linear-gradient(180deg,#f8e3e1,#faf8f3);transition:background 1s;}"+
    "body.tema-gris{background:#faf8f3;transition:background 1s;}"+
    ".pulso{animation:pulso 1.8s infinite;}@keyframes pulso{0%{transform:scale(1);}50%{transform:scale(1.05);}100%{transform:scale(1);}}";
  document.head.appendChild(st);
}
function aplicarTema(nivel){
  inyectarEstilosTema();
  var n=nivelClase(nivel);
  document.body.classList.remove("tema-verde","tema-naranja","tema-rojo","tema-gris");
  document.body.classList.add("tema-"+n);
}

/* Encuentra la estación más cercana a (la, lo) priorizando estaciones recientes,
   y actualiza toda la tarjeta de Inicio con sus contaminantes, ICA y semáforo. */
function actualizarEstacionCercanaEnInicio(posOpcional){
  var pos = posOpcional || ubicActual;
  if(!pos || pos.lat==null || pos.lon==null) return;
  if(!DATOS || !DATOS.length) return;
  var la = pos.lat, lo = pos.lon;
  var mejorReciente = null, dminReciente = 1e9;
  var mejorCualquiera = null, dminCualquiera = 1e9;
  DATOS.forEach(function(e){
    if(e.lat==null || e.lon==null) return;
    var d = haversine(la, lo, e.lat, e.lon);
    if(d < dminCualquiera){ dminCualquiera = d; mejorCualquiera = e; }
    if(e.reciente && d < dminReciente){ dminReciente = d; mejorReciente = e; }
  });
  var mejor = mejorReciente || mejorCualquiera;
  var dmin = mejorReciente ? dminReciente : dminCualquiera;
  if(!mejor) return;

  // Si la usuaria no ha fijado manualmente una UPZ en el selector, pintar su ubicación actual
  var zonaElegida = ($("sel-zona") && $("sel-zona").value) || "";
  if(!zonaElegida){
    var zonaSugeridaNombre = "Tu ubicación actual";
    if(ZONAS_ASOC && ZONAS_ASOC.zonas){
      var zCerca = null, dzMin = 1e9;
      ZONAS_ASOC.zonas.forEach(function(z){
        if(z.estado!=="ok" || !z.estacion) return;
        var dz = haversine(la, lo, z.estacion.lat, z.estacion.lon);
        if(dz < dzMin){ dzMin = dz; zCerca = z; }
      });
      if(zCerca && dzMin <= 4.5){
        zonaSugeridaNombre = "Tu ubicación (" + zCerca.nombre + ")";
      }
    }
    var distKm = Math.round(dmin * 10) / 10;
    var semObj = mejor.semaforo || (mejor.reciente && mejor.nivel && mejor.nivel!=="gris" ? {
      ica: mejor.icaEst != null ? mejor.icaEst : mejor.aqi,
      nivel: mejor.nivel
    } : null);
    pintarZona({
      zona: { id: String(mejor.uid), nombre: mejor.nombre, distancia_km: distKm },
      estado: mejor.reciente && semObj ? "ok" : "sin_dato_reciente",
      estacion: {
        uid: mejor.uid,
        nombre: mejor.nombre,
        medido_iso: mejor.medido_iso,
        medido_bogota: mejor.medido_bogota,
        medido_texto: mejor.medido_texto || mejor.hora,
        reciente: mejor.reciente,
        aqi_waqi: mejor.aqi,
        aqi_pm25: mejor.aqi_pm25,
        aqi_pm10: mejor.aqi_pm10,
        aqi_no2: mejor.no2,
        aqi_so2: mejor.so2,
        aqi_o3: mejor.o3,
        aqi_co: mejor.co
      },
      semaforo: semObj,
      indice_waqi: mejor.aqi,
      aqi_pm25: mejor.aqi_pm25,
      aqi_pm10: mejor.aqi_pm10,
      aqi_no2: mejor.no2,
      aqi_so2: mejor.so2,
      aqi_o3: mejor.o3,
      aqi_co: mejor.co,
      no2: mejor.no2,
      so2: mejor.so2,
      o3: mejor.o3,
      co: mejor.co,
      atribucion: mejor.atribucion || ["WAQI", "RMCAB / CAR"]
    });
    if($("sel-zona") && $("sel-zona").value !== String(mejor.uid)){
      $("sel-zona").value = String(mejor.uid);
    }
  }
}

/* Sincroniza periódicamente las estaciones, el modo prueba, la vista actual y las alertas
   para que cualquier cambio desde el administrador o la API se refleje sin recargar. */
function sincronizarDatosServidor(forzarVista){
  fetch("/api/waqi/estaciones").then(function(r){ return r.ok ? r.json() : null; }).then(function(d){
    if(!d) return;
    var cinta = $("prueba-cinta"); if(cinta) cinta.hidden = !d.modo_prueba;
    var lista = mapearEstaciones(d);
    var hash = JSON.stringify({
      mp: Boolean(d.modo_prueba),
      c: d.guardado_en || "",
      ests: lista.map(function(x){ return [x.uid, x.nivel, x.aqi, x.icaEst, x.reciente]; })
    });
    var cambio = (hash !== ULTIMO_ESTADO_HASH) || Boolean(forzarVista);
    ULTIMO_ESTADO_HASH = hash;
    if(lista.length){
      DATOS = lista;
      aplicarDemoCinta(false);
      pintarLista();
    }
    if(cambio){
      if(mapa){
        pintarMarcadores();
        dibujarZonasMapa();
      }
      var selVal = ($("sel-zona") && $("sel-zona").value) || "";
      if(selVal){
        verZona(selVal);
      } else if(ubicActual){
        actualizarEstacionCercanaEnInicio(ubicActual);
      } else {
        actualizarRecoPersonalizada();
      }
      var vInd = $("vista-indicaciones");
      if(vInd && !vInd.hidden){
        verIndicaciones();
      }
    }
  }).catch(function(){});
  if(TOKEN) cargarNotificaciones();
}

function iniciarSincronizacionContinua(){
  if(SYNC_TIMER) clearInterval(SYNC_TIMER);
  SYNC_TIMER = setInterval(function(){ sincronizarDatosServidor(false); }, 10000);
}

function iniciarUbicacionAutomatica(){
  onUbicacion(function(pos){
    var la=pos.lat, lo=pos.lon;
    enviarUbicacionServidor(la, lo);
    if(mapa && window.L){
      if(!ubicMarker){ ubicMarker=L.marker([la,lo]).addTo(mapa).bindPopup("Tu ubicación"); }
      else { ubicMarker.setLatLng([la,lo]); }
    }
    actualizarEstacionCercanaEnInicio(pos);
  });
  iniciarUbicacionContinua();
  // Solicitar de inmediato una primera lectura de ubicación al abrir la app
  getUbicacionActual().then(function(pos){
    window.mobiaireUbicacion(pos.lat, pos.lon);
  }).catch(function(){});
}

/* ============ Arranque ============ */
function init(){
  recuperarSesion();
  if(TOKEN) sincronizarServicioNativo();
  document.querySelectorAll(".nav__b").forEach(function(b){ b.onclick=function(){ cambiarVista(b.dataset.vista); }; });
  $("btn-busca-zona").onclick=abrirHoja;
  $("btn-otra-zona").onclick=abrirHoja;
  $("btn-cerrar-hoja").onclick=cerrarHoja;
  $("hoja-fondo").onclick=cerrarHoja;
  $("btn-mi-ubic").onclick=usarMiUbicacion;
  $("btn-ver-mapa").onclick=function(){ cambiarVista("estaciones"); };
  if($("btn-ver-indicaciones")) $("btn-ver-indicaciones").onclick=function(){ cambiarVista("indicaciones"); };
  $("btn-expandir").onclick=function(){
    var m=$("mapa"); m.classList.toggle("expandido");
    $("btn-expandir").textContent = m.classList.contains("expandido")?"Reducir mapa":"Expandir mapa";
    if(mapa) setTimeout(function(){ mapa.invalidateSize(); },60);
  };
  $("sel-zona").onchange=function(){
    var id=this.value;
    if(id){ location.hash="zona="+id; verZona(id); guardarZonaEnPerfil(id); }
    else { location.hash=""; }
  };
  window.addEventListener("hashchange", function(){
    var h=(location.hash||"").replace(/^#zona=/,"");
    if(h){ $("sel-zona").value=h; verZona(h); }
  });
  if($("btn-perfil")) $("btn-perfil").onclick = abrirPerfil;
  if($("modal-perfil")) $("modal-perfil").onclick = function(){ cerrarModal("hoja-perfil","modal-perfil"); };
  if($("btn-sos")) $("btn-sos").onclick = abrirSOS;
  if($("modal-sos")) $("modal-sos").onclick = function(){ cerrarModal("hoja-sos","modal-sos"); };
  if($("btn-notif")) $("btn-notif").onclick = abrirNotificaciones;
  if($("modal-notif")) $("modal-notif").onclick = function(){ cerrarModal("hoja-notif","modal-notif"); };
  if($("modal-detalle")) $("modal-detalle").onclick = cerrarDetalle;
  if($("detalle-cerrar")) $("detalle-cerrar").onclick = cerrarDetalle;

  cargarRecos().then(function(){
    cargarZonas();
    cargarEstaciones();
  });
  iniciarUbicacionAutomatica();
  iniciarSincronizacionContinua();

  // Pantalla de login al abrir (con opción "Explorar app").
  if(!USUARIO){ setTimeout(function(){ abrirModal("hoja-perfil","modal-perfil"); renderPerfil(); }, 250); }
  else {
    api("/api/auth/me").then(function(u){ USUARIO=u; guardarSesion(TOKEN,u); })
      .catch(function(){});
    iniciarNotificaciones();
  }
  setTimeout(function(){ if(mapa) dibujarZonasMapa(); }, 1200);
}
document.addEventListener("DOMContentLoaded", init);

/* ============ Log cliente -> servidor ============ */
function logCliente(msg, level){
  try{
    fetch("/api/log",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({msg:String(msg),level:level||"info",user:(USUARIO?USUARIO.username:"anon")})}).catch(function(){});
  }catch(e){}
}
window.addEventListener("error", function(e){ logCliente("JS ERROR: "+(e.message||e)+" @ "+(e.filename||"")+":"+(e.lineno||""), "error"); });
window.addEventListener("unhandledrejection", function(e){ logCliente("PROMISE REJECTION: "+(e.reason&&e.reason.message?e.reason.message:e.reason), "error"); });
logCliente("app cargada", "info");
