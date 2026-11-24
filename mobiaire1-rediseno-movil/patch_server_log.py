p="web/server.py"
s=open(p,encoding="utf-8").read()

# 1. Add /api/log route in do_POST
old_post='''        if ruta == "/api/admin/test-mode":
            return self._admin_test_mode_set()
        self._error("Ruta no encontrada", 404)'''
new_post='''        if ruta == "/api/admin/test-mode":
            return self._admin_test_mode_set()
        if ruta == "/api/log":
            return self._client_log()
        self._error("Ruta no encontrada", 404)'''
assert old_post in s
s=s.replace(old_post,new_post)

# 2. Add GET /api/log route
old_get='''        if camino == "/api/admin/refresh/status":
            return self._admin_refresh_status()'''
new_get='''        if camino == "/api/admin/refresh/status":
            return self._admin_refresh_status()
        if camino == "/api/log":
            return self._client_log_get()'''
assert old_get in s
s=s.replace(old_get,new_get)

# 3. Add methods before _salud
needle="    def _salud(self):"
methods='''    def _client_log(self):
        body = self._body_json()
        msg = body.get("msg") or body.get("message") or ""
        nivel = body.get("level") or "info"
        import datetime
        linea = "{} [{}] {}".format(datetime.datetime.now().isoformat(timespec="seconds"), nivel, msg)
        try:
            ruta = RAIZ / "ingesta" / "client.log"
            ruta.parent.mkdir(parents=True, exist_ok=True)
            with open(ruta, "a", encoding="utf-8") as f:
                f.write(linea + "\\n")
        except Exception:
            pass
        return self._json({"ok": True})

    def _client_log_get(self):
        ruta = RAIZ / "ingesta" / "client.log"
        if not ruta.exists():
            return self._json({"log": ""})
        try:
            lineas = ruta.read_text(encoding="utf-8").splitlines()
            return self._json({"log": "\\n".join(lineas[-200:])})
        except Exception as e:
            return self._error(str(e), 500)

'''
assert needle in s
s=s.replace(needle, methods+needle, 1)

# 4. Override log_message to write to server.log
old_lm='''    # logs sobrios (sin datos personales)
    def log_message(self, formato, *args):
        sys.stderr.write("%s - %s\\n" % (self.address_string(), formato % args))'''
new_lm='''    # logs sobrios (sin datos personales) + archivo
    def log_message(self, formato, *args):
        linea = "%s - %s\\n" % (self.address_string(), formato % args)
        sys.stderr.write(linea)
        try:
            ruta = RAIZ / "ingesta" / "server.log"
            ruta.parent.mkdir(parents=True, exist_ok=True)
            with open(ruta, "a", encoding="utf-8") as f:
                f.write(linea)
        except Exception:
            pass'''
assert old_lm in s
s=s.replace(old_lm,new_lm)

open(p,"w",encoding="utf-8").write(s)
print("ok")
