p="android-app/app/src/main/java/com/mobiaire/maternal/MainActivity.kt"
s=open(p,encoding="utf-8").read()
s=s.replace('private val defaultUrl = "https://mobiairematerno.loca.lt"','private val defaultUrl = "http://192.168.20.37:8000"')
open(p,"w",encoding="utf-8").write(s)
print("ok")
