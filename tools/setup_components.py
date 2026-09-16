# -*- coding: utf-8 -*-
"""v9.16 组件一键安装 —— ffmpeg / torch / paddle / CosyVoice / 权重

requirements.txt 与 requirements-models.txt 只管纯 pip 包。
录屏要的 ffmpeg 是系统二进制，torch / paddlepaddle 要按本机 CUDA 选轮子，
CosyVoice 还要 git clone 源码 + 9GB 权重 —— 这些都塞不进 requirements。
本脚本把这几步串起来。

  python tools/setup_components.py              # 交互菜单
  python tools/setup_components.py --check      # 只体检，不装任何东西
  python tools/setup_components.py --all        # 全装（含 9GB 权重）
  python tools/setup_components.py --only ffmpeg --only torch
  python tools/setup_components.py --all --mirror modelscope   # 国内走魔搭

可选 key：ffmpeg / deps / torch / paddle / cosyvoice-code / cosyvoice-deps
          / weights-ocr / weights-whisper / weights-cosyvoice
          / paddle-cpu（GPU 版 paddle 反复导入失败时，手动强制切 CPU）
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

def _find_root() -> Path:
    """定位项目根：从本文件往上找，认 mainv916.py。

    这样脚本放在 tools/ 或直接放项目根都不会算错。
    """
    here = Path(__file__).resolve()
    for cand in (here.parent, *here.parents):
        if (cand / "mainv916.py").is_file():
            return cand
    return here.parents[1] if len(here.parents) > 1 else here.parent


ROOT = _find_root()
MODEL = ROOT / "model"
PY = sys.executable

IS_WIN = sys.platform.startswith("win")

# CUDA 版本 → torch 官方 wheel index。就近向下取。
TORCH_INDEX = [
    (12.8, "cu128"),
    (12.6, "cu126"),
    (12.4, "cu124"),
    (12.1, "cu121"),
    (11.8, "cu118"),
]
# paddlepaddle-gpu 官方只放了这几档
PADDLE_INDEX = [
    (12.6, "cu126"),
    (11.8, "cu118"),
]
PADDLE_VER = "3.2.1"
# VL 管线需要 doc-parser extras（拉齐 paddlex[ocr]）；裸装 paddleocr 会误报就绪
OCR_PIP_SPEC = "paddleocr[doc-parser]>=3.6.0"


# ── 小工具 ────────────────────────────────────────────────────────────

def hr(title: str = ""):
    print("\n" + "=" * 60)
    if title:
        print(title)
        print("=" * 60)


def run(cmd, check=True, **kw):
    """回显并执行。cmd 为 list。"""
    print("  $", " ".join(str(c) for c in cmd))
    try:
        subprocess.run(cmd, check=check, **kw)
        return True
    except FileNotFoundError:
        print("  ! 找不到可执行文件：", cmd[0])
        return False
    except subprocess.CalledProcessError as e:
        print("  ! 命令失败，返回码", e.returncode)
        return False


def pip(*args) -> bool:
    # 默认超时较短，遇到慢源（比如 paddlepaddle.org.cn）容易连续 ReadTimeout。
    return run([PY, "-m", "pip", "install", "--default-timeout=120", *args])


def ask(prompt: str, default: bool = True) -> bool:
    # --yes：采用每道题的默认选项（不会对「仍要重装吗？[y/N]」误点是）
    if ASSUME_YES:
        return default
    tail = "[Y/n]" if default else "[y/N]"
    try:
        a = input(f"{prompt} {tail} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not a:
        return default
    return a in {"y", "yes", "是"}


ASSUME_YES = False


# ── 环境探测 ──────────────────────────────────────────────────────────

def detect_cuda() -> float | None:
    """从 nvidia-smi 读驱动支持的最高 CUDA 版本；无 N 卡返回 None。"""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return None
    m = re.search(r"CUDA Version:\s*([\d.]+)", out)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def pick_tag(cuda: float | None, table) -> str | None:
    """CUDA 版本 → wheel tag；无卡或太老返回 None（走 CPU）。"""
    if cuda is None:
        return None
    for need, tag in table:
        if cuda >= need:
            return tag
    return None


def has_pkg(name: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def probe_import(mod: str, timeout: int = 40) -> tuple[bool, str]:
    """真的起一个子进程 import 一次，抓 has_pkg 测不出来的运行时错误

    （典型如 Windows 下 cuDNN DLL 加载失败）。返回 (是否成功, 最后一行错误)。
    """
    try:
        r = subprocess.run([PY, "-c", f"import {mod}"],
                           capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        return False, str(e)
    if r.returncode == 0:
        return True, ""
    err = (r.stderr or r.stdout or "").strip()
    lines = [l for l in err.splitlines() if l.strip()]
    return False, (lines[-1] if lines else "导入失败（无错误输出）")


# ── 各组件安装 ────────────────────────────────────────────────────────

def do_ffmpeg() -> bool:
    """录屏组件。ffmpeg 是系统二进制，只能靠包管理器。"""
    hr("① ffmpeg（区域录屏）")
    if shutil.which("ffmpeg"):
        print("  已在 PATH 中：", shutil.which("ffmpeg"))
        return True
    if not IS_WIN:
        print("  非 Windows，请用系统包管理器安装，例如：")
        print("    sudo apt install ffmpeg   /   brew install ffmpeg")
        return False

    if shutil.which("winget"):
        print("  用 winget 安装（会弹 UAC，选「是」）")
        ok = run([
            "winget", "install", "--id", "Gyan.FFmpeg", "-e",
            "--accept-package-agreements", "--accept-source-agreements",
        ], check=False)
        if ok:
            print("\n  ★ winget 装完需要【新开一个命令行窗口】PATH 才生效。")
            return True
    if shutil.which("scoop"):
        if run(["scoop", "install", "ffmpeg"], check=False):
            return True

    print("  自动安装失败。手动任选一种：")
    print("    winget install Gyan.FFmpeg")
    print("    scoop install ffmpeg")
    print("    或从 https://www.gyan.dev/ffmpeg/builds/ 下载后把 bin 目录加进 PATH")
    return False


def do_deps() -> bool:
    """requirements-models.txt：sounddevice / faster-whisper / paddleocr[doc-parser] / onnxruntime"""
    hr("② 模型相关 pip 依赖（语音转写等）")
    req = ROOT / "requirements-models.txt"
    if not req.is_file():
        print("  ! 找不到", req)
        return False
    return pip("-r", str(req))


def do_torch(cuda: float | None) -> bool:
    """语音克隆的运行基础。必须按本机 CUDA 选轮子，不能写进 requirements。"""
    hr("③ PyTorch（语音克隆）")
    tag = pick_tag(cuda, TORCH_INDEX)
    if has_pkg("torch"):
        try:
            import torch  # noqa
            print(f"  已安装 torch {torch.__version__}，CUDA 可用={torch.cuda.is_available()}")
        except Exception:
            print("  已安装 torch（导入失败，可能是版本损坏）")
        if not ask("  仍要重新安装吗？", default=False):
            return True
    if tag:
        print(f"  检测到 CUDA {cuda} → 使用 {tag} 轮子")
        ok = pip("torch", "torchaudio", "--index-url",
                 f"https://download.pytorch.org/whl/{tag}")
        if not ok:
            print(f"  ! {tag} 源可能还没有对应版本，回退 CPU 版")
            ok = pip("torch", "torchaudio")
        return ok
    print("  未检测到 NVIDIA 显卡（或驱动过旧）→ 装 CPU 版")
    print("  注意：CPU 跑 CosyVoice 合成会很慢，但功能可用。")
    return pip("torch", "torchaudio")


def _install_paddlepaddle_cpu() -> bool:
    """装 CPU 版 paddlepaddle。

    CPU 版 wheel 其实 PyPI 官方源上就有（只有 GPU 版因为体积/CUDA 变体
    太多没放 PyPI），优先走 PyPI——更快也更稳，官方定制源
    paddlepaddle.org.cn 实测出现过连续 ReadTimeout。PyPI 万一没有这个
    具体版本，再回退官方源（带更长超时重试一次）。
    """
    print("  先试 PyPI 官方源（CPU 版本来就在上面）")
    if pip(f"paddlepaddle=={PADDLE_VER}"):
        return True
    print("  ! PyPI 没有这个版本或安装失败，回退 paddlepaddle.org.cn（重试一次）")
    for attempt in (1, 2):
        if pip(f"paddlepaddle=={PADDLE_VER}", "-i",
               "https://www.paddlepaddle.org.cn/packages/stable/cpu/"):
            return True
        if attempt == 1:
            print("  ! 又超时了，最后重试一次...")
    return False


def do_paddle(cuda: float | None) -> bool:
    """截图 OCR 的底座。同样要按卡选；装完会真的 import 一次探测 DLL 问题。"""
    hr("④ PaddlePaddle（截图 OCR）")
    if has_pkg("paddle"):
        ok_import, err = probe_import("paddle")
        if ok_import:
            print("  已安装，导入正常")
            if not ask("  仍要重新安装吗？", default=False):
                return True
        else:
            print(f"  已安装但导入失败：{err}")
            print("  （GPU 版 cuDNN 的 DLL 问题很常见，下面会重装尝试修复）")

    tag = pick_tag(cuda, PADDLE_INDEX)
    if tag:
        print(f"  检测到 CUDA {cuda} → GPU 版 {tag}")
        pip(f"paddlepaddle-gpu=={PADDLE_VER}", "-i",
            f"https://www.paddlepaddle.org.cn/packages/stable/{tag}/")
        ok_import, err = probe_import("paddle")
        if ok_import:
            # auto paddleocr after paddle
            return do_paddleocr()
        print(f"  ! GPU 版装完仍无法导入：{err}")
        if "cudnn" in err.lower() or "winerror 127" in err.lower():
            print("  这是 Windows 下 paddlepaddle-gpu 常见的 cuDNN DLL 加载问题，")
            print("  多半是系统缺 zlibwapi.dll（cuDNN 依赖它）。两条路：")
            print("    A. 手动装 zlibwapi.dll：NVIDIA cuDNN 文档里下载，放进")
            print("       C:\\Windows\\System32，重启后重试 GPU 版")
            print("    B. 更省心：换 CPU 版 paddle（截图 OCR 不追求实时，CPU 够用）")
        if not ask("  现在自动切换到 CPU 版 paddle 吗？", default=True):
            return False
        run([PY, "-m", "pip", "uninstall", "-y", "paddlepaddle-gpu"], check=False)
    else:
        print("  未检测到可用 CUDA → CPU 版")

    ok = _install_paddlepaddle_cpu()
    if ok:
        ok_import, err = probe_import("paddle")
        if not ok_import:
            print(f"  ! CPU 版仍无法导入：{err}")
            return False
        # auto paddleocr after paddle
        return do_paddleocr()
    return ok


def do_paddle_cpu_force() -> bool:
    """跳过所有判断，直接卸 GPU 版、强制装 CPU 版。用于 GPU 版反复修不好时。"""
    hr("④' 强制改用 CPU 版 paddle")
    run([PY, "-m", "pip", "uninstall", "-y", "paddlepaddle-gpu", "paddlepaddle"], check=False)
    ok = _install_paddlepaddle_cpu()
    if ok:
        ok_import, err = probe_import("paddle")
        if not ok_import:
            print(f"  ! 仍无法导入：{err}")
            return False
        # auto paddleocr after paddle
        if not do_paddleocr():
            return False
    return ok


def do_paddleocr() -> bool:
    """安装/升级 PaddleOCR-VL 运行库（含 paddlex[ocr] extras）。"""
    hr("④b PaddleOCR-VL 运行库（paddleocr[doc-parser]）")
    print(f"  安装 {OCR_PIP_SPEC}")
    print("  （裸装 paddleocr 不含 doc-parser 时，VL-1.6 初始化会报缺 paddlex[ocr]）")
    return pip("-U", OCR_PIP_SPEC)


def probe_ocr_vl_pipeline() -> tuple[bool, str]:
    """真测 PaddleOCRVL：在子进程里按 app 真实顺序跑（先 torch 再 paddle）。

    旧实现在当前进程直接 `from paddleocr import PaddleOCRVL`，会先载 paddle
    再被 paddlex/modelscope 拉进 torch，Windows 上常报 WinError 1114 / c10.dll，
    造成「装完其实可用、体检却失败」。app 的 utils/ocr_job.py 已用
    `_preload_torch_first()` 避开；体检必须同一路径。
    """
    import json
    import tempfile

    model_dir = MODEL / "PaddleOCR-VL-1.6"
    if not (model_dir / "config.json").is_file():
        try:
            sys.path.insert(0, str(ROOT))
            from utils.ocr_util import find_vl_model_dir
            model_dir = Path(find_vl_model_dir())
        except Exception:
            pass
    if not (model_dir / "config.json").is_file():
        return False, f"缺权重 {model_dir}"

    td = tempfile.mkdtemp(prefix="da_ocr_probe_")
    meta_path = str(Path(td) / "meta.json")
    out_path = str(Path(td) / "out.json")
    err_path = str(Path(td) / "err.txt")
    meta = {
        "action": "probe",
        "model_dir": str(model_dir),
        "out": out_path,
    }
    try:
        Path(meta_path).write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8"
        )
        env = os.environ.copy()
        env.setdefault("OMP_NUM_THREADS", "1")
        env.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
        env.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        r = subprocess.run(
            [PY, "-m", "utils.ocr_job", meta_path],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
        payload = {}
        if Path(out_path).is_file():
            try:
                payload = json.loads(Path(out_path).read_text(encoding="utf-8"))
            except Exception:
                payload = {}
        if payload.get("ok"):
            return True, "就绪"

        msg = (payload.get("error") or "").strip()
        if not msg and Path(err_path).is_file():
            msg = Path(err_path).read_text(encoding="utf-8", errors="replace").strip()
        if not msg:
            msg = ((r.stderr or r.stdout or "").strip() or f"probe exit={r.returncode}")
        low = msg.lower()
        if "paddlex" in low or "doc-parser" in low or "additional dependencies" in low:
            msg += f' → 请执行: python -m pip install -U "{OCR_PIP_SPEC}"'
        if "c10.dll" in low or "winerror 1114" in low or "winerror 127" in low:
            msg += " → torch/paddle DLL 冲突时试: python tools/setup_components.py --only paddle-cpu --yes"
        if len(msg) > 240:
            msg = msg[:240] + "…"
        return False, msg
    except subprocess.TimeoutExpired:
        return False, "真测超时（>300s）。可设 PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True 后重试"
    except Exception as e:
        msg = str(e).strip() or type(e).__name__
        if len(msg) > 240:
            msg = msg[:240] + "…"
        return False, msg
    finally:
        try:
            shutil.rmtree(td, ignore_errors=True)
        except Exception:
            pass


def do_cosyvoice_code() -> bool:
    """CosyVoice 官方推理源码（几十 MB 的 Python 代码，不是模型）。"""
    hr("⑤ CosyVoice 运行库源码")
    dest = MODEL / "CosyVoice"
    if (dest / "cosyvoice" / "cli" / "cosyvoice.py").is_file():
        print("  已存在：", dest)
        return True
    if not shutil.which("git"):
        print("  ! 没有 git。先装：winget install Git.Git")
        return False
    if dest.exists() and any(dest.iterdir()):
        print(f"  ! {dest} 已存在但不完整，请先删掉再跑本脚本")
        return False
    MODEL.mkdir(parents=True, exist_ok=True)
    return run(["git", "clone", "--recursive",
                "https://github.com/FunAudioLLM/CosyVoice.git", str(dest)], check=False)


# CosyVoice 官方 requirements.txt 里这些不装：
# torch/torchaudio —— 已按本机 CUDA 单独装过，版本被官方锁死会互相打架；
# deepspeed / tensorrt-cu12* / onnxruntime-gpu —— 官方文件里标了仅 linux，Windows 用不上；
# --extra-index-url —— 那行是给 torch 用的 pip 源，这里不装 torch 就不需要。
_COSYVOICE_SKIP_PREFIX = ("--extra-index-url", "torch==", "torchaudio==",
                          "deepspeed", "tensorrt-cu12", "onnxruntime-gpu")

# requirements.txt 解析失败时的兜底清单（跟官方仓库同步过，Windows 可装的部分）
_COSYVOICE_DEPS_FALLBACK = [
    "conformer==0.3.2", "diffusers==0.29.0", "fastapi==0.115.6",
    "fastapi-cli==0.0.4", "gdown==5.1.0", "gradio==5.4.0", "grpcio==1.57.0",
    "grpcio-tools==1.57.0", "hydra-core==1.3.2", "HyperPyYAML==1.2.3",
    "inflect==7.3.1", "librosa==0.10.2", "lightning==2.2.4",
    "matplotlib==3.7.5", "modelscope==1.20.0", "networkx==3.1",
    "omegaconf==2.3.0", "onnx==1.16.0", "onnxruntime==1.18.0",
    "openai-whisper==20231117", "protobuf==4.25", "pyarrow==18.1.0",
    "pydantic==2.7.0", "pyworld==0.3.4", "rich==13.7.1", "soundfile==0.12.1",
    "tensorboard==2.14.0", "transformers==4.51.3", "x-transformers==2.11.24",
    "uvicorn==0.30.0", "wetext==0.0.4", "wget==3.2",
]


def _freeze_constraints() -> Path | None:
    """把已装好的 torch/torchaudio 版本钉进一个 constraints 文件。

    下面循环单独装 30 多个包，每个包自己也会声明依赖 torch（且大多不锁版本）。
    没有这层约束的话，某个包的解析过程可能会把已经按你显卡装好的 CUDA 版
    torch 悄悄换成别的版本——而这几个包恰恰是最不该被动到的。

    不锁 numpy：CosyVoice 要求的 onnxruntime==1.18.0 是按 NumPy 1.x
    的 ABI 编译的，装在 NumPy 2.x 环境里会在 import 时报
    `AttributeError: _ARRAY_API not found`。CosyVoice 官方 requirements.txt
    本来就把 numpy 锁在 1.26.4，就是为了配这个 onnxruntime 版本——
    如果这里连 numpy 也锁死，反而会挡住这个必要的降级。
    """
    try:
        from importlib.metadata import version, PackageNotFoundError
    except ImportError:
        return None
    pins = []
    for name in ("torch", "torchaudio"):
        try:
            pins.append(f"{name}=={version(name)}")
        except PackageNotFoundError:
            pass
    if not pins:
        return None
    f = ROOT / "tools" / "_pip_constraints.tmp.txt"
    try:
        f.write_text("\n".join(pins) + "\n", encoding="utf-8")
        return f
    except Exception:
        return None


def do_cosyvoice_deps() -> bool:
    """CosyVoice 自己的 pip 依赖（hyperpyyaml 等）。

    只 clone 源码不够——CosyVoice 的推理代码本身还要几十个包，
    这是之前脚本漏掉的一步，报错 `No module named 'hyperpyyaml'` 就是这里。

    三个坑一起修：
    1. openai-whisper==20231117 的 setup.py 是老式写法，装的时候要用 pkg_resources，
       而新版 setuptools（81+）已经把 pkg_resources 删了，装它就会炸
       （CosyVoice 官方仓库也有人报过一模一样的问题：
       github.com/FunAudioLLM/CosyVoice/issues/1844）。
       光在当前环境里把 setuptools 钉住不够 —— pip 默认会给每个包单独建一个
       "隔离构建环境"，那个临时环境会自己去装最新版 setuptools，不认你钉的版本。
       所以这里钉住版本之后，还要对这类老包加 --no-build-isolation，
       让它直接用当前环境里（已经钉住的）setuptools，不再单独建临时环境。
    2. 之前是把几十个包塞进一条 pip 命令，只要有一个包构建失败，
       pip 会整条回滚、一个都不装——包括本来能装上的 hyperpyyaml。
       改成一个个装，互不连累。
    3. 单独装的每个包自己也会拉依赖（很多都不锁 torch 版本），
       解析过程可能悄悄把你按显卡装好的 torch 换掉。用 constraints
       文件把 torch/torchaudio 钉死，只允许装、不允许动版本（numpy 不锁，
       原因见 _freeze_constraints 的说明——这里恰恰需要让它能降级）。
    """
    hr("⑥ CosyVoice 自身依赖（hyperpyyaml 等，clone 源码不会带上）")
    req = MODEL / "CosyVoice" / "requirements.txt"
    pkgs: list[str] = []
    if req.is_file():
        for line in req.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith(_COSYVOICE_SKIP_PREFIX):
                continue
            pkgs.append(line)
    if not pkgs:
        print("  未找到/解析不了 model/CosyVoice/requirements.txt，用内置兜底清单")
        pkgs = list(_COSYVOICE_DEPS_FALLBACK)

    print("  先钉住 setuptools<81（新版删了 pkg_resources，老包装不了）")
    pip("setuptools<81", "wheel")

    cons = _freeze_constraints()
    if cons:
        print("  已钉住 torch/torchaudio/numpy 当前版本，避免被子依赖悄悄换掉")
    cons_args = ("-c", str(cons)) if cons else ()

    # 用老式 setup.py + pkg_resources 的包，需要 --no-build-isolation 才装得上
    legacy_pkg_prefix = ("openai-whisper", "pyworld")

    print(f"  共 {len(pkgs)} 个包（已跳过 torch/torchaudio/deepspeed/tensorrt，"
          "这些要么单独装过要么只给 Linux 用），逐个装，互不连累：")
    failed = []
    try:
        for spec in pkgs:
            is_legacy = spec.lower().startswith(legacy_pkg_prefix)
            extra = ("--no-build-isolation",) if is_legacy else ()
            if not pip(*extra, *cons_args, spec):
                failed.append(spec)
    finally:
        if cons:
            try:
                cons.unlink()
            except OSError:
                pass

    if failed:
        print(f"\n  ! {len(failed)} 个没装上：" + "、".join(failed))
        print("  其余包已正常装好，不受影响。常见原因：")
        print("    - openai-whisper / pyworld 等老包需要 C++ 编译环境：")
        print("      装 Visual Studio Build Tools，勾选「使用 C++ 的桌面开发」")
        print("    - 也可以单独重试：pip install <包名>")
        return False
    return True


def do_weights(key: str, mirror: str) -> bool:
    """转交给项目自带的 download_models.py。"""
    names = {
        "weights-ocr": "paddleocr",
        "weights-whisper": "whisper-small",
        "weights-cosyvoice": "cosyvoice",
    }
    titles = {
        "weights-ocr": "⑦ PaddleOCR-VL 权重（约 1.9GB）",
        "weights-whisper": "⑧ faster-whisper-small 权重",
        "weights-cosyvoice": "⑨ Fun-CosyVoice3-0.5B 权重（约 9GB，很慢）",
    }
    hr(titles[key])
    if weights_ready(key):
        print("  已在 model/ 中，跳过下载（不会重复下载、不会覆盖）。")
        return True
    return run([PY, str(ROOT / "tools" / "download_models.py"),
                "--only", names[key], "--mirror", mirror], check=False)


# ── 体检：直接调用程序自己的检测函数，与「关于」页结果一致 ──────────────

def weights_ready(key: str) -> bool:
    """复用 download_models.py 的 _ready()，判断权重是否已在 model/ 里。

    与下载脚本同一套判据，避免菜单说要下、实际却 [跳过] 的错位。
    """
    local = {"weights-ocr": "PaddleOCR-VL-1.6",
             "weights-whisper": "faster-whisper-small",
             "weights-cosyvoice": "Fun-CosyVoice3-0.5B"}.get(key)
    if not local:
        return False
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        from download_models import _ready  # type: ignore
        return bool(_ready(local))
    except Exception:
        d = MODEL / local
        if not d.is_dir():
            return False
        return any(f.is_file() and f.stat().st_size > 1_000_000
                   and ".git" not in f.parts for f in d.rglob("*"))


def check_all(verbose: bool = True) -> dict:
    """返回各组件状态字典；verbose 时同时打印体检表。"""
    if verbose:
        hr("组件体检")
    sys.path.insert(0, str(ROOT))
    st: dict = {}
    rows = []

    # 录屏
    try:
        from utils.region_recorder import find_ffmpeg
        p = find_ffmpeg()
        st["ffmpeg"] = bool(p)
        rows.append(("录屏组件 (ffmpeg)", bool(p), p or "未找到，运行 --only ffmpeg"))
    except Exception as e:
        st["ffmpeg"] = False
        rows.append(("录屏组件 (ffmpeg)", False, f"检测异常 {e}"))

    # 语音转写。voice_input 顶层 import 了 PyQt5，主依赖没装时会炸，故降级直查包。
    try:
        from utils.voice_input import voice_deps_ok
        ok, missing = voice_deps_ok()
    except Exception:
        missing = [n for n, m in (("sounddevice", "sounddevice"),
                                  ("numpy", "numpy"),
                                  ("faster-whisper", "faster_whisper"))
                   if not has_pkg(m)]
        ok = not missing
    st["voice_pkg"] = ok
    st["weights-whisper"] = weights_ready("weights-whisper")
    rows.append(("语音组件 (whisper)", ok,
                 "就绪" if ok else "缺 " + ", ".join(missing)))

    # OCR
    try:
        from utils.ocr_util import find_vl_model_dir
        d = find_vl_model_dir()
        has_w = os.path.isfile(os.path.join(d, "config.json"))
    except Exception:
        has_w = weights_ready("weights-ocr")
        d = str(MODEL / "PaddleOCR-VL-1.6")
    has_p = has_pkg("paddle") and has_pkg("paddleocr")
    paddle_err = ""
    if has_p:
        ok_import, paddle_err = probe_import("paddle")
        if not ok_import:
            has_p = False
    st["ocr_pkg"] = has_p
    st["weights-ocr"] = has_w
    ocr_ok = False
    if has_w and has_p:
        # 真测 VL 管线，避免缺 paddlex[ocr] 时误报就绪
        print("  …正在真测 PaddleOCR-VL 初始化（首次可能较慢）")
        ok_vl, ocr_note = probe_ocr_vl_pipeline()
        ocr_ok = ok_vl
        st["ocr_vl"] = ok_vl
    elif not has_w:
        ocr_note = "缺权重 " + str(d)
    elif paddle_err:
        ocr_note = f"paddle 已装但导入失败：{paddle_err}"
    else:
        ocr_note = f"缺 paddlepaddle / {OCR_PIP_SPEC}"
    rows.append(("截图 OCR", ocr_ok if (has_w and has_p) else False, ocr_note))

    # 语音克隆：三层缺口分开报
    try:
        from utils.cosyvoice_clone import weights_status
        cs = weights_status()
        has_w = bool(cs.get("has_weights"))
        has_s = bool(cs.get("has_source"))
        has_t = bool(cs.get("torch_pkg"))
    except Exception:
        # 同样绕开 PyQt5：直接看磁盘
        has_w = weights_ready("weights-cosyvoice")
        has_s = (MODEL / "CosyVoice" / "cosyvoice" / "cli" / "cosyvoice.py").is_file()
        has_t = has_pkg("torch")
    st["weights-cosyvoice"] = has_w
    st["cosyvoice-code"] = has_s
    st["torch"] = has_t
    # 第四层：CosyVoice 自己的 pip 依赖（hyperpyyaml 等）。clone 源码不会带上这些。
    has_deps = has_pkg("hyperpyyaml")
    st["cosyvoice-deps"] = has_deps
    miss = []
    if not has_w:
        miss.append("权重 Fun-CosyVoice3-0.5B")
    if not has_s:
        miss.append("源码 model/CosyVoice")
    if not has_t:
        miss.append("PyTorch")
    if not has_deps:
        miss.append("依赖包 hyperpyyaml 等（见 --only cosyvoice-deps）")
    rows.append(("语音克隆 (CosyVoice)", not miss,
                 "就绪" if not miss else "缺 " + "、".join(miss)))

    if verbose:
        print()
        for name, ok, note in rows:
            print(f"  [{'OK' if ok else '--'}] {name:24} {note}")
        print()
    st["_all_ok"] = all(r[1] for r in rows)
    return st


# ── 主流程 ────────────────────────────────────────────────────────────

ALL_KEYS = ["ffmpeg", "deps", "torch", "paddle", "paddleocr", "cosyvoice-code", "cosyvoice-deps",
            "weights-ocr", "weights-whisper", "weights-cosyvoice"]

# 仅用于交互菜单里 --only 校验；不进「全部」，只用于反复修不好时的手动兜底
EXTRA_KEYS = ["paddle-cpu"]

# 仅用于估算「还需下载多少」，已在本地的不计入
DL_SIZE_GB = {"weights-ocr": 1.9, "weights-whisper": 0.5, "weights-cosyvoice": 9.0}

# key → 该项是否已满足（读体检结果）
DONE_OF = {
    "ffmpeg": "ffmpeg",
    "deps": "voice_pkg",
    "torch": "torch",
    "paddle": "ocr_pkg",
    "paddleocr": "ocr_vl",
    "cosyvoice-code": "cosyvoice-code",
    "cosyvoice-deps": "cosyvoice-deps",
    "weights-ocr": "weights-ocr",
    "weights-whisper": "weights-whisper",
    "weights-cosyvoice": "weights-cosyvoice",
}

MENU_SPEC = [
    ("1", ["ffmpeg"], "修录屏"),
    ("2", ["deps", "weights-whisper"], "修语音转写"),
    ("3", ["deps", "paddle", "paddleocr", "weights-ocr"], "修截图 OCR"),
    ("4", ["torch", "cosyvoice-code", "cosyvoice-deps", "weights-cosyvoice"], "修语音克隆"),
    ("5", ALL_KEYS, "全部"),
]


def _todo(keys, st) -> list:
    """剔除已满足的项，只留真正要做的。"""
    return [k for k in keys if not st.get(DONE_OF.get(k, k))]


def _menu_note(keys, st) -> str:
    """按实际缺口生成说明，已就绪的不再吓唬人报体积。"""
    todo = _todo(keys, st)
    if not todo:
        return "已就绪，无需操作"
    gb = sum(DL_SIZE_GB.get(k, 0) for k in todo)
    desc = "、".join({
        "ffmpeg": "ffmpeg", "deps": "pip 依赖", "torch": "torch",
        "paddle": "paddle", "cosyvoice-code": "源码", "cosyvoice-deps": "依赖包",
        "paddleocr": "PaddleOCR-VL 运行库", "weights-ocr": "OCR 权重", "weights-whisper": "whisper 权重",
        "weights-cosyvoice": "克隆权重",
    }[k] for k in todo)
    return f"{desc}" + (f"，需下载约 {gb:.1f}GB" if gb else "，无需下载")


def build_menu(st) -> list:
    rows = []
    missing = _todo(ALL_KEYS, st)
    if missing:
        gb = sum(DL_SIZE_GB.get(k, 0) for k in missing)
        rows.append(("0", missing,
                     "只装缺的（推荐）—— " +
                     ("无需下载" if not gb else f"需下载约 {gb:.1f}GB")))
    for code, keys, title in MENU_SPEC:
        rows.append((code, _todo(keys, st), f"{title}（{_menu_note(keys, st)}）"))
    rows.append(("c", [], "只体检，不装"))
    return rows


def main() -> int:
    global ASSUME_YES
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--only", action="append", default=[],
                    help="指定组件，可重复；可选 " + " / ".join(ALL_KEYS + EXTRA_KEYS))
    ap.add_argument("--all", action="store_true", help="全部安装")
    ap.add_argument("--check", action="store_true", help="只体检")
    ap.add_argument("--mirror", choices=["hf", "modelscope"], default="hf",
                    help="权重下载源，国内建议 modelscope")
    ap.add_argument("--yes", action="store_true", help="所有确认一律 y")
    args = ap.parse_args()
    ASSUME_YES = args.yes

    print("桌面助手 v9.16 · 组件安装")
    print("项目根：", ROOT)
    print("Python：", PY)

    cuda = detect_cuda()
    if cuda:
        print(f"显卡：检测到 NVIDIA，驱动支持 CUDA {cuda}")
    else:
        print("显卡：未检测到 NVIDIA（将使用 CPU 版 torch / paddle）")

    if args.check:
        check_all()
        return 0

    keys = list(args.only)
    if args.all:
        # --all 也只做缺的，不重复下已在 model/ 里的权重
        keys = _todo(ALL_KEYS, check_all(verbose=False))
        if not keys:
            print("\n所有组件均已就绪，无需安装。")
            check_all()
            return 0
    if not keys:
        st = check_all()
        menu = build_menu(st)
        if st.get("_all_ok"):
            print("所有组件均已就绪。如需重装可用 --only <组件名>。")
            return 0
        hr("要装什么？")
        for k, _, desc in menu:
            print(f"  {k}. {desc}")
        print("  q. 退出")
        try:
            c = input("\n选择：").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return 0
        if c in {"", "q"}:
            return 0
        if c == "c":
            check_all()
            return 0
        hit = next((m for m in menu if m[0] == c), None)
        if not hit:
            print("无效选择")
            return 2
        keys = hit[1]
        if not keys:
            print("该项已就绪，无需操作。")
            return 0

    bad = [k for k in keys if k not in ALL_KEYS and k not in EXTRA_KEYS]
    if bad:
        print("未知组件：", ", ".join(bad))
        return 2

    # 固定顺序执行，避免先下权重后装库
    results = {}
    order = ALL_KEYS + [k for k in EXTRA_KEYS if k in keys and k not in ALL_KEYS]
    for k in [x for x in order if x in keys]:
        if k == "ffmpeg":
            results[k] = do_ffmpeg()
        elif k == "deps":
            results[k] = do_deps()
        elif k == "torch":
            results[k] = do_torch(cuda)
        elif k == "paddle":
            results[k] = do_paddle(cuda)
        elif k == "paddle-cpu":
            results[k] = do_paddle_cpu_force()
        elif k == "paddleocr":
            results[k] = do_paddleocr()
        elif k == "cosyvoice-code":
            results[k] = do_cosyvoice_code()
        elif k == "cosyvoice-deps":
            results[k] = do_cosyvoice_deps()
        else:
            results[k] = do_weights(k, args.mirror)

    hr("安装结果")
    for k, ok in results.items():
        print(f"  [{'OK' if ok else '!!'}] {k}")

    check_all()
    print("★ 装完请【完全退出并重启】桌面助手，再到「关于 → 模型与组件」点「检测」。")
    if any(k == "ffmpeg" for k in results):
        print("★ 装过 ffmpeg 的话，要新开一个命令行窗口 PATH 才生效。")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
