import urllib.request, ssl, re
ctx = ssl.create_default_context()
ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
r = urllib.request.urlopen('https://noxtang.com/', context=ctx, timeout=10)
html = r.read().decode()
js = re.search(r'src="\/assets\/(index-[^"]+\.js)"', html)
css = re.search(r'href="\/assets\/(index-[^"]+\.css)"', html)
print('JS:', js.group(1) if js else 'NONE')
print('CSS:', css.group(1) if css else 'NONE')
if js:
    r2 = urllib.request.urlopen('https://noxtang.com/assets/' + js.group(1), context=ctx, timeout=10)
    data = r2.read()
    print('JS size:', len(data), 'has React:', b'React' in data[:200])
