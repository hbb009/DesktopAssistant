"""Cancellable network events; this module never writes task state or files."""
import http.client
import json
import queue
import socket
import threading
import time


def network_events(url, timeout, payload, token):
    events=queue.Queue(maxsize=32)
    finished=threading.Event()
    kind=http.client.HTTPSConnection if url.scheme=='https' else http.client.HTTPConnection
    connection=kind(url.hostname,url.port,timeout=min(10,timeout))
    sockets=[]
    started=time.monotonic()

    def abort():
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        connection.close()

    def put(value):
        while not finished.is_set() and not token.event.is_set():
            try:
                events.put(value,timeout=0.1)
                return
            except queue.Full:
                pass

    def worker():
        token.add_callback(abort)
        try:
            connection.connect()
            sockets.append(connection.sock)
            connection.sock.settimeout(timeout)
            token.check()
            connection.request('POST',url.path.rstrip('/')+'/api/chat',json.dumps(payload,ensure_ascii=False).encode('utf-8'),{'Content-Type':'application/json'})
            response=connection.getresponse()
            put(('status',response.status))
            if response.status>=400:
                put(('http_error',(response.status,response.read(16384).decode('utf-8',errors='replace'))))
                return
            while not finished.is_set():
                token.check()
                line=response.readline(4*1024*1024)
                if not line:
                    return
                put(('line',line))
                try:
                    item=json.loads(line)
                    if not isinstance(item,dict) or item.get('done') or item.get('error'):
                        return
                except ValueError:
                    return
        except Exception as exc:
            put(('error',exc))
        finally:
            token.remove_callback(abort)
            abort()
            put(('eof',None))

    thread=threading.Thread(target=worker,daemon=True,name='agent-v2-network')
    thread.start()
    try:
        while True:
            token.check()
            if time.monotonic()-started > max(30, timeout*3):
                raise TimeoutError('模型请求超过总时限。')
            try:
                event,value=events.get(timeout=0.1)
            except queue.Empty:
                continue
            token.check()
            if event=='error':
                raise value
            if event=='eof':
                return
            yield event,value
    finally:
        finished.set()
        abort()
