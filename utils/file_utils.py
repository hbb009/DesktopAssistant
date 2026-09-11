import os

def ensure_dir(path):
    """确保目录存在，不存在就创建"""
    if not os.path.exists(path):
        os.makedirs(path)
