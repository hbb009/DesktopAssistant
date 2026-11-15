# -*- coding: utf-8 -*-
"""阅读器用程序里的 CosyVoice 朗读。模型在独立进程里只加载一次。"""
import ctypes
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time


_FIRST = 32
_REST = 96
_MIN = 16
_ALIAS = 'rdwav'
_ENDS = '。！？!?…\n'


def speak_pieces(text, first=_FIRST, rest=_REST):
    """按句切开，再收成一小段一小段。第一段短一些，好尽快出声。"""
    raw = (text or '').replace('\u00a0', ' ').strip()
    if not raw:
        return []
    first = max(8, int(first))
    rest = max(first, int(rest))
    bits = []
    buf = []
    for ch in raw:
        buf.append(ch)
        ended = ch in _ENDS or (ch in '；;' and len(buf) >= 12)
        if ended or len(buf) >= rest:
            piece = ''.join(buf).strip()
            if piece:
                bits.append(piece)
            buf = []
    tail = ''.join(buf).strip()
    if tail:
        bits.append(tail)
    chunks = []
    cur = ''
    limit = first
    for bit in bits:
        if cur and len(cur) + len(bit) > limit:
            chunks.append(cur)
            cur = bit
            limit = rest
        else:
            cur += bit
        if len(cur) >= limit:
            chunks.append(cur)
            cur = ''
            limit = rest
    if cur:
        chunks.append(cur)
    merged = []
    for ch in chunks:
        if merged and len(ch) < _MIN and len(merged[-1]) + len(ch) <= rest + _MIN:
            merged[-1] += ch
        else:
            merged.append(ch)
    return merged


def _weights_dir(path):
    if not path or not os.path.isdir(path):
        return False
    names = set()
    try:
        names = set(os.listdir(path))
    except OSError:
        return False
    return 'cosyvoice3.yaml' in names or 'llm.pt' in names or 'llm.rl.pt' in names


def _wav_seconds(path):
    try:
        import wave
        with wave.open(path, 'rb') as w:
            n = int(w.getnframes() or 0)
            r = int(w.getframerate() or 0)
            return (float(n) / float(r)) if r > 0 else 0.0
    except Exception:
        return 0.0


TTS_MISSING = 'TTS: 未安装'


def tts_model_label(root=None):
    """朗读实际会用的模型短名（目录名）。只看配置和权重目录，不加载模型。"""
    root = os.path.abspath(root or '')
    model_dir = ''
    user_path = os.path.join(root, 'userdata', 'user.txt')
    try:
        with open(user_path, encoding='utf-8') as f:
            vc = json.load(f).get('voice_clone') or {}
        if isinstance(vc, dict):
            model_dir = (vc.get('model_dir') or '').strip()
    except Exception:
        model_dir = ''
    if not _weights_dir(model_dir):
        cand = os.path.join(root, 'model', 'Fun-CosyVoice3-0.5B')
        model_dir = cand if _weights_dir(cand) else ''
    if not _weights_dir(model_dir):
        return TTS_MISSING
    name = os.path.basename(os.path.normpath(model_dir))
    return name or TTS_MISSING


def resolve_voice(root, gender=None):
    """朗读用的模型和参考音。

    gender 为「男」或「女」时用模型自带的两段示例音，不读克隆页保存的参考音。
    不传时，有保存的参考音就用它，否则用男声示例。
    """
    root = os.path.abspath(root or '')
    out = {
        'ok': False,
        'error': '',
        'model_dir': '',
        'home': '',
        'prompt_wav': '',
        'prompt_text': '',
        'instruct': '',
        'speed': 1.0,
        'builtin_voice': False,
    }
    model_dir = ''
    prompt = ''
    prompt_text = ''
    instruct = ''
    speed = 1.0
    user_path = os.path.join(root, 'userdata', 'user.txt')
    try:
        with open(user_path, encoding='utf-8') as f:
            vc = json.load(f).get('voice_clone') or {}
        if isinstance(vc, dict):
            model_dir = (vc.get('model_dir') or '').strip()
            prompt = (vc.get('prompt_wav') or '').strip()
            prompt_text = (vc.get('prompt_text') or '').strip()
            instruct = (vc.get('instruct') or '').strip()
            try:
                speed = float(vc.get('speed') or 1.0)
            except (TypeError, ValueError):
                speed = 1.0
    except Exception:
        pass
    if not _weights_dir(model_dir):
        cand = os.path.join(root, 'model', 'Fun-CosyVoice3-0.5B')
        model_dir = cand if _weights_dir(cand) else ''
    home = os.path.join(root, 'model', 'CosyVoice')
    marker = os.path.join(home, 'cosyvoice', 'cli', 'cosyvoice.py')
    if not os.path.isfile(marker):
        home = ''
    builtin = False
    gender = (gender or '').strip()
    if gender in ('男', '女'):
        name = 'zero_shot_prompt.wav' if gender == '女' else 'cross_lingual_prompt.wav'
        prompt = os.path.join(root, 'model', 'CosyVoice', 'asset', name)
        prompt_text = ''
        instruct = ''
        builtin = True
    elif not prompt or not os.path.isfile(prompt):
        prompt = os.path.join(root, 'model', 'CosyVoice', 'asset', 'cross_lingual_prompt.wav')
        prompt_text = ''
        instruct = ''
        builtin = True
    if not _weights_dir(model_dir):
        out['error'] = '还没有 Fun-CosyVoice3 模型'
        return out
    if not home:
        out['error'] = '还没有 CosyVoice 运行库'
        return out
    if not os.path.isfile(prompt):
        out['error'] = '没有可用的参考音频'
        return out
    dur = _wav_seconds(prompt)
    if dur and dur > 30.0:
        out['error'] = '参考音频超过 30 秒，请先在语音克隆里裁短'
        return out
    speed = max(0.5, min(2.0, speed))
    out.update({
        'ok': True,
        'model_dir': model_dir,
        'home': home,
        'prompt_wav': prompt,
        'prompt_text': '' if builtin else prompt_text,
        'instruct': '' if builtin else instruct,
        'speed': speed,
        'builtin_voice': builtin,
    })
    return out


def brief_error(text):
    raw = (text or '').strip()
    if not raw:
        return '语音合成失败'
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    line = lines[-1] if lines else raw
    line = re.sub(r'^[A-Za-z_]+(?:Error|Exception):\s*', '', line)
    return line[:80] or '语音合成失败'


def _mci(cmd):
    buf = ctypes.create_unicode_buffer(260)
    err = ctypes.windll.winmm.mciSendStringW(cmd, buf, 260, None)
    return int(err or 0), buf.value


class _WavPlay:
    """用 Windows 自带的播放接口播 wav，可以暂停、接着播。"""

    def __init__(self):
        self.open = False
        self.held = False
        self._lock = threading.Lock()

    def start(self, path):
        with self._lock:
            self._close_locked()
            err, _msg = _mci('open "%s" type waveaudio alias %s' % (path, _ALIAS))
            if err:
                raise RuntimeError('这段声音播不出来')
            self.open = True
            self.held = False
            err, _msg = _mci('play %s' % _ALIAS)
            if err:
                self._close_locked()
                raise RuntimeError('这段声音播不出来')

    def _mode(self):
        _err, mode = _mci('status %s mode' % _ALIAS)
        return (mode or '').strip().lower()

    def sync(self, paused):
        with self._lock:
            if not self.open:
                return
            if paused:
                # play 刚发出时 MCI 可能还没进入 playing，第一次 pause 会落空。
                # held 只表示“想暂停”，只要还在播就要再试一次。
                mode = self._mode()
                if mode == 'playing' or not self.held:
                    _mci('pause %s' % _ALIAS)
                self.held = True
            elif self.held:
                _mci('resume %s' % _ALIAS)
                self.held = False

    def finished(self):
        with self._lock:
            if not self.open:
                return True
            if self.held:
                return False
            mode = self._mode()
            return mode not in ('playing', 'paused', 'seeking')

    def progress(self):
        """当前播放进度 0..1。拿不到就返回 None。"""
        with self._lock:
            if not self.open:
                return None
            _err, pos = _mci('status %s position' % _ALIAS)
            _err2, length = _mci('status %s length' % _ALIAS)
        try:
            p, total = float(pos or 0), float(length or 0)
        except (TypeError, ValueError):
            return None
        if total <= 0:
            return None
        return max(0.0, min(1.0, p / total))

    def close(self):
        with self._lock:
            self._close_locked()

    def _close_locked(self):
        if self.open:
            _mci('stop %s' % _ALIAS)
            _mci('close %s' % _ALIAS)
        self.open = False
        self.held = False


class CosyReader:
    """后台合成并播放。界面线程只收 status / play / done / error。

    合成和播放分两条线程：一边念，一边提前合成后面 1～2 段，中间尽量不断。
    synth / player 仅给测试用，正式朗读会走独立的模型进程。
    """

    _PREFETCH = 2

    def __init__(self, root, emit, synth=None, player=None):
        self._root = os.path.abspath(root or '')
        self._emit = emit
        self._synth_fn = synth
        self._q = queue.Queue()
        self._pause = threading.Event()
        self._live = 0
        self._proc = None
        self._log = None
        self._play = player if player is not None else _WavPlay()
        self._dir = ''
        self._voice_ready = False
        self._thread = threading.Thread(target=self._loop, name='reader-cosy', daemon=True)
        self._thread.start()

    def start(self, text, gen, cfg):
        self._pause.clear()
        self._live = int(gen)
        self._q.put(('say', (text, int(gen), cfg)))

    def pause(self):
        self._pause.set()
        try:
            self._play.sync(True)
        except Exception:
            pass

    def resume(self):
        self._pause.clear()
        try:
            self._play.sync(False)
        except Exception:
            pass

    def stop(self):
        self._live = -1
        self._pause.clear()
        try:
            self._play.close()
        except Exception:
            pass
        self._q.put(('stop', None))

    def progress(self):
        player = getattr(self, '_play', None)
        fn = getattr(player, 'progress', None) if player is not None else None
        if not fn:
            return None
        try:
            return fn()
        except Exception:
            return None

    def close(self):
        self._live = -1
        self._pause.clear()
        try:
            self._play.close()
        except Exception:
            pass
        self._q.put(('quit', None))
        proc = self._proc
        self._proc = None
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass

    def _loop(self):
        while True:
            cmd, payload = self._q.get()
            if cmd == 'quit':
                self._shutdown()
                break
            if cmd == 'stop':
                try:
                    self._play.close()
                except Exception:
                    pass
                continue
            if cmd != 'say':
                continue
            text, gen, cfg = payload
            try:
                self._say(text, gen, cfg or {})
            except Exception as exc:
                try:
                    self._play.close()
                except Exception:
                    pass
                self._emit_if_live(gen, 'error', brief_error(str(exc)))

    def _say(self, text, gen, cfg):
        pieces = speak_pieces(text)
        if not pieces:
            self._emit_if_live(gen, 'done', '')
            return
        warm = self._synth_fn is not None or (self._proc is not None and self._proc.poll() is None)
        self._emit_if_live(gen, 'status', '合成中…' if warm else '加载模型…')
        if self._synth_fn is None:
            self._ensure_proc(cfg)
            if not self._alive(gen):
                return
            if not self._voice_ready:
                self._rpc({'action': 'load', **self._voice_meta(cfg)}, 600, gen)
                if self._alive(gen):
                    self._voice_ready = True
            if not self._alive(gen):
                return

        ready = queue.Queue(maxsize=self._PREFETCH)

        def produce():
            try:
                for i, piece in enumerate(pieces):
                    if not self._alive(gen):
                        break
                    if i == 0:
                        self._emit_if_live(gen, 'status', '合成中…')
                    t0 = time.perf_counter()
                    path = self._synth(cfg, piece, gen)
                    synth_s = time.perf_counter() - t0
                    if not path or not self._alive(gen):
                        self._drop(path)
                        break
                    while self._alive(gen):
                        try:
                            ready.put(('wav', path, piece, synth_s), timeout=0.05)
                            break
                        except queue.Full:
                            continue
                    else:
                        self._drop(path)
                        break
            finally:
                try:
                    ready.put(('end', '', '', 0.0), timeout=2)
                except Exception:
                    pass

        worker = threading.Thread(target=produce, name='reader-cosy-synth', daemon=True)
        worker.start()
        first = True
        try:
            while self._alive(gen):
                while self._pause.is_set() and self._alive(gen):
                    self._play.sync(True)
                    time.sleep(0.05)
                if not self._alive(gen):
                    break
                try:
                    kind, path, piece, synth_s = ready.get(timeout=0.05)
                except queue.Empty:
                    continue
                if kind != 'wav':
                    if self._alive(gen):
                        self._emit_if_live(gen, 'done', '')
                    return
                # 合成等候期间点了暂停：wav 先拿着，等继续后再开播，避免刚 start 就丢暂停。
                while self._pause.is_set() and self._alive(gen):
                    time.sleep(0.05)
                if not self._alive(gen):
                    self._drop(path)
                    break
                wav_s = _wav_seconds(path)
                rtf = (synth_s / wav_s) if wav_s > 0 else 0.0
                self._log_line(
                    '朗读分段 chars=%d synth_s=%.2f wav_s=%.2f rtf=%.3f'
                    % (len(piece or ''), synth_s, wav_s, rtf)
                )
                self._play.start(path)
                if self._pause.is_set():
                    self._play.sync(True)
                # 真正开播后再通知界面画朗读线，预取合成不触发。
                self._emit_if_live(gen, 'play', piece)
                if first:
                    self._emit_if_live(gen, 'status', '朗读中')
                    first = False
                self._wait_play(gen)
                self._play.close()
                self._drop(path)
        finally:
            while True:
                try:
                    kind, path, _piece, _s = ready.get_nowait()
                except queue.Empty:
                    break
                if kind == 'wav':
                    self._drop(path)
            try:
                self._play.close()
            except Exception:
                pass

    def _voice_meta(self, cfg):
        return {
            'model_dir': cfg.get('model_dir') or '',
            'cosyvoice_home': cfg.get('home') or '',
            'prompt_wav': cfg.get('prompt_wav') or '',
            'prompt_text': cfg.get('prompt_text') or '',
            'instruct': cfg.get('instruct') or '',
            'speed': cfg.get('speed') or 1.0,
            'seed': 42,
        }

    def _synth(self, cfg, text, gen):
        if self._synth_fn is not None:
            return self._synth_fn(text, gen)
        fd, path = tempfile.mkstemp(prefix='read-', suffix='.wav', dir=self._work_dir())
        os.close(fd)
        meta = self._voice_meta(cfg)
        meta['action'] = 'synth'
        meta['tts_text'] = text
        meta['out'] = path
        data = self._rpc(meta, 180, gen)
        if not data:
            self._drop(path)
            return ''
        out = (data.get('out') or path) if isinstance(data, dict) else path
        if not os.path.isfile(out):
            raise RuntimeError('合成完成但没有声音文件')
        return out

    def _rpc(self, meta, timeout, gen):
        proc = self._ensure_proc(meta)
        work = self._work_dir()
        stamp = str(time.time_ns())
        meta_path = os.path.join(work, 'meta-%s.json' % stamp)
        result_path = os.path.join(work, 'result-%s.json' % stamp)
        body = dict(meta)
        body['result'] = result_path
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(body, f, ensure_ascii=False)
        if not self._alive(gen):
            return None
        proc.stdin.write(meta_path + '\n')
        proc.stdin.flush()
        deadline = time.time() + int(timeout)
        while time.time() < deadline:
            if not self._alive(gen):
                return None
            if os.path.isfile(result_path) and os.path.getsize(result_path) > 0:
                try:
                    with open(result_path, encoding='utf-8') as f:
                        data = json.load(f)
                except Exception:
                    time.sleep(0.05)
                    continue
                if not isinstance(data, dict) or not data.get('ok'):
                    raise RuntimeError(brief_error((data or {}).get('error') if isinstance(data, dict) else ''))
                return data
            if proc.poll() is not None:
                raise RuntimeError(self._tail() or '语音进程已退出')
            time.sleep(0.05)
        if not self._alive(gen):
            return None
        raise RuntimeError('语音合成超时')

    def _wait_play(self, gen):
        while self._alive(gen):
            paused = self._pause.is_set()
            self._play.sync(paused)
            if paused:
                time.sleep(0.05)
                continue
            if self._play.finished():
                return
            time.sleep(0.05)

    def _alive(self, gen):
        return self._live == int(gen)

    def _emit_if_live(self, gen, kind, text):
        if not self._alive(gen):
            return
        fn = self._emit
        if fn is None:
            return
        try:
            fn(int(gen), kind, text or '')
        except Exception:
            pass

    def _log_line(self, text):
        line = (text or '').rstrip()
        if not line:
            return
        try:
            path = os.path.join(self._root, 'log', 'reader_cosy.log')
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except Exception:
            pass

    def _work_dir(self):
        if not self._dir:
            self._dir = os.path.join(self._root, 'temp', 'reader-cosy')
            os.makedirs(self._dir, exist_ok=True)
        return self._dir

    def _ensure_proc(self, cfg):
        proc = self._proc
        if proc is not None and proc.poll() is None:
            return proc
        self._voice_ready = False
        self._shutdown()
        work = self._work_dir()
        log_dir = os.path.join(self._root, 'log')
        try:
            os.makedirs(log_dir, exist_ok=True)
            self._log = open(os.path.join(log_dir, 'reader_cosy.log'), 'ab')
        except Exception:
            self._log = open(os.path.join(work, 'reader_cosy.log'), 'ab')
        env = os.environ.copy()
        env.setdefault('OMP_NUM_THREADS', '1')
        env.setdefault('KMP_DUPLICATE_LIB_OK', 'TRUE')
        env.setdefault('TQDM_DISABLE', '1')
        env['PYTHONIOENCODING'] = 'utf-8'
        env.pop('QT_PLUGIN_PATH', None)
        env.pop('QT_QPA_PLATFORM_PLUGIN_PATH', None)
        home = (cfg.get('home') or cfg.get('cosyvoice_home') or '').strip()
        if home:
            env['COSYVOICE_HOME'] = home
            env['PYTHONPATH'] = home + os.pathsep + env.get('PYTHONPATH', '')
        frozen = bool(getattr(sys, 'frozen', False)) or hasattr(sys, '_MEIPASS')
        if frozen:
            cmd = [sys.executable, '--cosyvoice-serve']
        else:
            cmd = [sys.executable, '-m', 'utils.cosyvoice_job', '--serve']
        kw = {
            'cwd': self._root,
            'env': env,
            'stdin': subprocess.PIPE,
            'stdout': subprocess.DEVNULL,
            'stderr': self._log,
            'text': True,
            'encoding': 'utf-8',
            'errors': 'replace',
            'bufsize': 1,
        }
        if sys.platform == 'win32':
            kw['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)
        self._proc = subprocess.Popen(cmd, **kw)
        return self._proc

    def _tail(self):
        path = os.path.join(self._root, 'log', 'reader_cosy.log')
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                raw = f.read()
        except Exception:
            return ''
        return brief_error(raw[-1500:])

    def _drop(self, path):
        if not path:
            return
        try:
            os.remove(path)
        except OSError:
            pass

    def _shutdown(self):
        proc = self._proc
        self._proc = None
        self._voice_ready = False
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
        log = self._log
        self._log = None
        if log is not None:
            try:
                log.close()
            except Exception:
                pass
