# MobiAire Materno — app iOS (cliente)

Cliente iOS que carga la app desde el servidor (mismo modelo que el APK Android).
Es un `WKWebView` nativo: **no** es una PWA ni se instala desde Safari.

## Requisitos
- Un **Mac** con **Xcode 15+** (Apple no permite compilar iOS en Windows/Linux).
- Una cuenta de desarrollador Apple (gratuita sirve para pruebas en un iPhone propio).

## Crear el proyecto en Xcode
1. Xcode → *File ▸ New ▸ Project ▸ iOS ▸ App*.
   - Product Name: `MobiAireMaterno`
   - Interface: **Storyboard** · Language: **Swift**
2. Borra los archivos `ViewController.swift`, `AppDelegate.swift` e `Info.plist`
   que genera Xcode y arrastra los de esta carpeta (`ios-app/MobiAireMaterno/`).
3. En *Signing & Capabilities* elige tu equipo (Team).
4. Verifica que `Info.plist` esté como *Info.plist File* en *Build Settings*.

## Ejecutar
- Conecta un iPhone por USB, selecciónalo como destino y pulsa ▶.
- Al abrir, la app pide la URL del servidor (la IP del PC, ej. `http://192.168.20.37:8000`).
  Esa IP se guarda y se reutiliza.

## Alternativa sin Xcode (no recomendada)
No hay forma de generar un `.ipa` en Windows. Para pruebas reales de iOS hace
falta macOS + Xcode (o un servicio de compilación en la nube).
