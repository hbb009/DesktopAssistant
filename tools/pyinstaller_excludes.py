# PyInstaller excludes for Desktop Assistant packaging (priority mis-collected deps).
# Usage in .spec Analysis(...):
#   from tools.pyinstaller_excludes import EXCLUDES, collect_excludes
#   excludes=EXCLUDES
# Or CLI: --exclude-module=pyarrow --exclude-module=modelscope ...

EXCLUDES = [
    # Not imported by app code; commonly pulled by PaddleOCR/HF trees
    "pyarrow",
    "modelscope",
    "matplotlib",
    "mpl_toolkits",
    "sklearn",
    "skimage",
    "Cython",
    "llvmlite",
    "numba",
    "tensorboard",
    "tensorboard_data_server",
    # Optional later (bigger product decisions):
    # "onnxruntime.capi.onnxruntime_providers_cuda",  # huge CUDA EP
    # Do NOT exclude by default (needed at runtime for features):
    # "transformers",  # PaddleOCR-VL
    # "cv2",           # paddleocr stack
    # "paddle", "paddleocr",
    # "ctranslate2", "faster_whisper",
    # "onnxruntime", "torch",
]

def collect_excludes():
    return list(EXCLUDES)
