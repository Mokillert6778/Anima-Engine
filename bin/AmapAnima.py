import os
import json
import zlib
import struct
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

# ============================================================
# AMAP 解密与解析核心
# ============================================================
MAGIC = b"AMAP"
FORMAT_VERSION = 1
_XOR_KEY = bytes([
    0x5A, 0x3F, 0x1C, 0x88, 0xE2, 0x7B, 0x40, 0xD9,
    0x16, 0xA3, 0x6E, 0xCD, 0x2F, 0x91, 0x04, 0xB7,
    0x58, 0xF1, 0x22, 0x8C, 0x6A, 0x3D, 0xD0, 0x74,
    0xEB, 0x19, 0x82, 0x45, 0xCE, 0x37, 0x0B, 0xA6,
    0x93, 0x60, 0x1E, 0xFD, 0x4A, 0xC7, 0x28, 0x85,
    0x72, 0xBC, 0x39, 0x06, 0xE8, 0x51, 0x9D, 0x2C,
    0x14, 0x7F, 0xB3, 0xC6, 0x08, 0xE5, 0x6B, 0x2A,
    0x9E, 0x43, 0xD1, 0x57, 0x86, 0x31, 0xFA, 0x10,
])

def _xor_fast(data: bytes) -> bytes:
    try:
        import numpy as np
        n = len(_XOR_KEY)
        key = np.frombuffer((_XOR_KEY * ((len(data) // n) + 1))[:len(data)], dtype=np.uint8)
        arr = np.frombuffer(data, dtype=np.uint8)
        return (arr ^ key).tobytes()
    except ImportError:
        n = len(_XOR_KEY)
        return bytes(b ^ _XOR_KEY[i % n] for i, b in enumerate(data))

def load_amap_raw(filepath):
    path = Path(filepath)
    if not path.exists(): raise FileNotFoundError("文件不存在")
    data = path.read_bytes()
    if len(data) < 12 or data[:4] != MAGIC:
        raise ValueError("无效的 .amap 文件 (Bad magic)")
    
    version = struct.unpack("<I", data[4:8])[0]
    if version != FORMAT_VERSION:
        raise ValueError(f"不支持的版本: {version}")
        
    compressed = _xor_fast(data[12:])
    try:
        text = zlib.decompress(compressed)
    except Exception as e:
        raise ValueError(f"解压失败: {e}")
        
    return json.loads(text.decode("utf-8"))

# ============================================================
# 数据转换逻辑
# ============================================================
def convert_amap_to_hmmap(amap_data, log_callback):
    blocks = []
    entities = []
    
    sky_cfg = amap_data.get("sky", {})
    sky_name = sky_cfg.get("config", "day_clear.txt")
    
    spawn_arr = amap_data.get("spawn", [0.0, 0.0])
    spawn = [float(spawn_arr[0]), float(spawn_arr[1])]
    
    for item in amap_data.get("geometry", []):
        g_type = item.get("type", "")
        pos = item.get("pos", [0, 0, 0])
        half = item.get("half", [1, 1, 1])
        
        if g_type == "box":
            blocks.append([
                float(pos[0]), float(pos[1]), float(pos[2]),
                float(half[0]), float(half[1]), float(half[2]),
                item.get("texture", "dev.png"),
                item.get("cuts", None),
                False
            ])
        elif g_type == "waterbox":
            blocks.append([
                float(pos[0]), float(pos[1]), float(pos[2]),
                float(half[0]), float(half[1]), float(half[2]),
                "water.png", None, True
            ])
        elif g_type == "brush":
            blocks.append({
                "__type__": "Brush",
                "data": item.get("brush_data", {}),
                "user_data": {
                    "texture": item.get("texture", "dev.png"),
                    "is_water": False,
                    "model": [1,0,0,0, 0,1,0,0, 0,0,1,0, pos[0],pos[1],pos[2],1]
                }
            })
            
    for ent in amap_data.get("entities", []):
        etype = ent.get("type", "unknown")
        pos = ent.get("pos", [0, 0, 0])
        params = ent.get("params", {})
        if etype == "light_environment": continue
        entities.append([etype, float(pos[0]), float(pos[1]), float(pos[2]), None, params])
        
    for a in amap_data.get("addons", []):
        if a.get("type") == "model":
            entities.append([a.get("name", "prop_static"), float(a["pos"][0]), float(a["pos"][1]), float(a["pos"][2]), None, {}])
            
    return {"blocks": blocks, "entities": entities, "spawn": spawn, "sky": sky_name}

# ============================================================
# GUI 界面 (418x422 完美复刻 + 纯白背景)
# ============================================================
class AMAPConverterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("AMAP to HMMAP Decompiler")
        
        # ★ 精准锁定 418 x 422 尺寸
        self.root.geometry("418x422")
        self.root.minsize(418, 422)
        self.root.maxsize(418, 422) # 锁死不让拉伸
        
        # ★ 全局纯白背景
        self.root.configure(bg="white")
        
        # 字体设定
        default_font = ('Microsoft YaHei', 9)
        
        # Tab 栏 (使用原生主题，自带白底)
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=2, pady=(2, 0))
        
        # 强制让 Tab 内部也是白色的
        style = ttk.Style()
        style.configure("TFrame", background="white")
        
        self.tab_files = tk.Frame(self.notebook, bg="white")
        self.notebook.add(self.tab_files, text="  Files  ")
        self.notebook.add(tk.Frame(self.notebook, bg="white"), text="  World  ")
        self.notebook.add(tk.Frame(self.notebook, bg="white"), text="  Entities  ")
        self.notebook.add(tk.Frame(self.notebook, bg="white"), text="  Textures  ")
        self.notebook.add(tk.Frame(self.notebook, bg="white"), text="  Other  ")
        
        # 主内容区
        main_content = tk.Frame(self.tab_files, bg="white")
        main_content.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # 列表区域
        list_frame = tk.Frame(main_content, bg="white")
        list_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        self.file_listbox = tk.Listbox(
            list_frame, 
            selectmode=tk.EXTENDED, 
            bg="white", 
            fg="black",
            relief="solid", 
            bd=1, 
            highlightthickness=0,
            font=default_font
        )
        self.file_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.file_listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.file_listbox.config(yscrollcommand=scrollbar.set)
        
        # 右侧按钮
        btn_frame = tk.Frame(main_content, bg="white")
        btn_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(6, 0))
        
        self.btn_add = tk.Button(btn_frame, text="Add", width=10, command=self.add_files, font=default_font, relief="groove", bd=1)
        self.btn_remove = tk.Button(btn_frame, text="Remove", width=10, command=self.remove_selected, font=default_font, relief="groove", bd=1)
        self.btn_remove_all = tk.Button(btn_frame, text="Remove all", width=10, command=self.remove_all, font=default_font, relief="groove", bd=1)
        
        self.btn_add.pack(side=tk.TOP, pady=(0, 5))
        self.btn_remove.pack(side=tk.TOP, pady=5)
        self.btn_remove_all.pack(side=tk.TOP, pady=5)
        
        # 提示文字
        tip_label = tk.Label(
            self.root, 
            text="Tip: drag and drop files/folders on the box above", 
            fg="gray", 
            bg="white", 
            font=("Arial", 8)
        )
        tip_label.pack(side=tk.TOP, anchor=tk.W, padx=6, pady=(0, 4))
        
        # 底部操作栏
        bottom_frame = tk.Frame(self.root, bg="white")
        bottom_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=6, pady=(0, 8))
        
        btn_defaults = tk.Button(bottom_frame, text="Defaults", width=8, font=default_font, relief="groove", bd=1)
        btn_defaults.pack(side=tk.LEFT)
        
        self.dark_var = tk.BooleanVar()
        chk_dark = tk.Checkbutton(bottom_frame, text="Dark theme", variable=self.dark_var, bg="white", font=default_font)
        chk_dark.pack(side=tk.LEFT, padx=(8, 0))
        
        self.log_var = tk.StringVar(value="")
        self.status_label = tk.Label(bottom_frame, textvariable=self.log_var, fg="#666", bg="white", font=default_font)
        self.status_label.pack(side=tk.LEFT, padx=4, fill=tk.X, expand=True)
        
        self.btn_convert = tk.Button(bottom_frame, text="Decompile", width=10, command=self.start_conversion, font=default_font, relief="groove", bd=1)
        self.btn_convert.pack(side=tk.RIGHT)
        
        self.files = []

    def add_files(self):
        filetypes = (("AMAP Map", "*.amap"), ("All files", "*.*"))
        paths = filedialog.askopenfilenames(title="选择 AMAP 文件", filetypes=filetypes)
        for p in paths:
            if p and p not in self.files:
                self.files.append(p)
                self.file_listbox.insert(tk.END, os.path.basename(p))

    def remove_selected(self):
        selected_indices = self.file_listbox.curselection()
        for index in reversed(selected_indices):
            self.file_listbox.delete(index)
            del self.files[index]

    def remove_all(self):
        self.file_listbox.delete(0, tk.END)
        self.files.clear()

    def log(self, msg):
        self.log_var.set(msg)
        self.root.update_idletasks()

    def start_conversion(self):
        if not self.files:
            messagebox.showwarning("提示", "请先添加 .amap 文件！")
            return
            
        self.btn_convert.config(state=tk.DISABLED)
        self.btn_add.config(state=tk.DISABLED)
        self.btn_remove.config(state=tk.DISABLED)
        self.btn_remove_all.config(state=tk.DISABLED)
        
        thread = threading.Thread(target=self.process_files)
        thread.daemon = True
        thread.start()

    def process_files(self):
        total = len(self.files)
        success = 0
        
        for i, amap_file in enumerate(self.files):
            self.log(f"转换中 [{i+1}/{total}] ...")
            out_file = str(Path(amap_file).with_suffix(".hmmap"))
            
            try:
                amap_json = load_amap_raw(amap_file)
                hmmap_json = convert_amap_to_hmmap(amap_json, self.log)
                
                with open(out_file, "w", encoding="utf-8") as f:
                    json.dump(hmmap_json, f, indent=2)
                    
                self.log(f"成功!")
                success += 1
            except Exception as e:
                self.log(f"失败: {str(e)}")
                print(f"Error: {e}")
                
        self.log(f"完成 {success}/{total}")
        self.root.after(0, self.reset_ui)
        
        def show_done():
            messagebox.showinfo("完成", f"成功生成 {success} 个 .hmmap 文件。")
        self.root.after(0, show_done)

    def reset_ui(self):
        self.btn_convert.config(state=tk.NORMAL)
        self.btn_add.config(state=tk.NORMAL)
        self.btn_remove.config(state=tk.NORMAL)
        self.btn_remove_all.config(state=tk.NORMAL)

if __name__ == "__main__":
    root = tk.Tk()
    app = AMAPConverterApp(root)
    root.mainloop()