import urllib.request
import urllib.error
try:
    urllib.request.urlopen('https://urban-octo-tribble-rouge.vercel.app/docs')
except urllib.error.HTTPError as e:
    print(e.read().decode('utf-8'))
