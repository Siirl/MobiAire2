p="android-app/app/src/main/java/com/mobiaire/maternal/MainActivity.kt"
s=open(p,encoding="utf-8").read()
s=s.replace("import android.webkit.WebView","import android.webkit.WebView\nimport android.webkit.WebView.setWebContentsDebuggingEnabled")
# insert call after super.onCreate
s=s.replace("        super.onCreate(savedInstanceState)","        super.onCreate(savedInstanceState)\n        WebView.setWebContentsDebuggingEnabled(true)")
open(p,"w",encoding="utf-8").write(s)
print("ok")
