import UIKit
import WebKit
import CoreLocation
import ContactsUI
import MessageUI

/// Cliente iOS de MobiAire Materno: un WKWebView que carga la app desde el
/// servidor (igual que el APK Android). El servidor es el conmutador; el
/// cliente nunca llama a la API de WAQI.
class ViewController: UIViewController, WKNavigationDelegate, WKUIDelegate, CLLocationManagerDelegate {

    var webView: WKWebView!
    let defaultURL = "http://192.168.20.37:8000"
    let locationManager = CLLocationManager()

    override func loadView() {
        let config = WKWebViewConfiguration()
        config.allowsInlineMediaPlayback = true
        config.preferences.javaScriptCanOpenWindowsAutomatically = true
        webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        view = webView
    }

    override func viewDidLoad() {
        super.viewDidLoad()
        locationManager.delegate = self
        locationManager.requestWhenInUseAuthorization()
        let guardada = UserDefaults.standard.string(forKey: "server_url")
        let url = guardada ?? defaultURL
        if let u = URL(string: url) {
            webView.load(URLRequest(url: u))
        }
        if guardada == nil {
            pedirServidor()
        }
    }

    /// Pide la URL del servidor la primera vez (igual que el APK Android).
    func pedirServidor() {
        let alert = UIAlertController(title: "Servidor MobiAire",
                                      message: "Ingresa la URL del servidor (PC):",
                                      preferredStyle: .alert)
        alert.addTextField { campo in
            campo.text = self.defaultURL
            campo.keyboardType = .URL
            campo.autocapitalizationType = .none
            campo.autocorrectionType = .no
        }
        alert.addAction(UIAlertAction(title: "Guardar", style: .default) { _ in
            let v = alert.textFields?.first?.text?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            let final = v.isEmpty ? self.defaultURL : v
            UserDefaults.standard.set(final, forKey: "server_url")
            if let u = URL(string: final) { self.webView.load(URLRequest(url: u)) }
        })
        alert.addAction(UIAlertAction(title: "Usar por defecto", style: .default) { _ in
            UserDefaults.standard.set(self.defaultURL, forKey: "server_url")
            if let u = URL(string: self.defaultURL) { self.webView.load(URLRequest(url: u)) }
        })
        alert.addAction(UIAlertAction(title: "Cancelar", style: .cancel))
        present(alert, animated: true)
    }

    // Permite que el JS use la ubicación automáticamente.
    func webView(_ webView: WKWebView,
                 requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                 initiatedByFrame frame: WKFrameInfo,
                 type: WKMediaCaptureType,
                 decisionHandler: @escaping (WKPermissionDecision) -> Void) {
        decisionHandler(.grant)
    }

    // Abre tel:, sms: y mailto: con las apps del sistema.
    func webView(_ webView: WKWebView,
                 decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let url = navigationAction.request.url,
           let scheme = url.scheme?.lowercased(),
           ["tel", "sms", "mailto"].contains(scheme) {
            UIApplication.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    // Diálogos nativos para alert()/confirm()/prompt() del JS.
    func webView(_ webView: WKWebView,
                 runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping () -> Void) {
        let alert = UIAlertController(title: "MobiAire Materno", message: message,
                                      preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "OK", style: .default) { _ in completionHandler() })
        present(alert, animated: true)
    }

    func webView(_ webView: WKWebView,
                 runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping (Bool) -> Void) {
        let alert = UIAlertController(title: "MobiAire Materno", message: message,
                                      preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "Cancelar", style: .cancel) { _ in completionHandler(false) })
        alert.addAction(UIAlertAction(title: "Aceptar", style: .default) { _ in completionHandler(true) })
        present(alert, animated: true)
    }

    func webView(_ webView: WKWebView,
                 runJavaScriptTextInputPanelWithPrompt prompt: String,
                 defaultText: String?,
                 initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping (String?) -> Void) {
        let alert = UIAlertController(title: "MobiAire Materno", message: prompt,
                                      preferredStyle: .alert)
        alert.addTextField { $0.text = defaultText }
        alert.addAction(UIAlertAction(title: "Cancelar", style: .cancel) { _ in completionHandler(nil) })
        alert.addAction(UIAlertAction(title: "Aceptar", style: .default) { _ in
            completionHandler(alert.textFields?.first?.text)
        })
        present(alert, animated: true)
    }

    func locationManager(_ manager: CLLocationManager, didChangeAuthorization status: CLAuthorizationStatus) {
        // El JS pedirá la posición; el permiso ya está concedido aquí.
    }
}
