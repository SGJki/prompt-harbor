SENSITIVE = {'authorization','proxy-authorization','cookie','set-cookie','host','content-length','connection','transfer-encoding'}

def sanitize(headers):
    return {k: v for k, v in headers.items() if k.lower() not in SENSITIVE}

