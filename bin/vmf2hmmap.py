import os
import re
import json
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

# ============================================================
# 核心 VMF 解析器 (保持独立，不依赖外部库)
# ============================================================
def tokenize_vmf(text):
    text = re.sub(r'//.*', '', text)
    token_regex = re.compile(r'"([^"]*)"|([^\s\{\}]+)|(\{|\})')
    for match in token_regex.finditer(text):
        if match.group(1) is not None:
            yield match.group(1)
        elif match.group(2) is not None:
            yield match.group(2)
        else:
            yield match.group(3)

def parse_vmf(text):
    """
    修复后的 VMF 解析器：使用栈式解析，正确处理 Block 和 Key-Value。
    修复了原代码中因给 dict 添加自定义属性 (_last_key) 导致的 AttributeError。
    """
    tokens = list(tokenize_vmf(text))
    stack = [{}]
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == '{':
            i += 1
            continue
        elif token == '}':
            if len(stack) > 1:
                stack.pop()
            i += 1
            continue

        if i + 1 < len(tokens):
            next_token = tokens[i+1]
            if next_token == '{':
                # 这是一个块结构 (Block)
                new_dict = {}
                parent = stack[-1]
                if token not in parent:
                    parent[token] = []
                elif not isinstance(parent[token], list):
                    parent[token] = [parent[token]]
                parent[token].append(new_dict)
                stack.append(new_dict)
                i += 2
            else:
                # 这是一个键值对 (Key-Value)
                stack[-1][token] = next_token
                i += 2
        else:
            i += 1
    return stack[0]

def convert_coord(src_x, src_y, src_z):
    # Source(X, Y, Z) -> Engine(X, Z, -Y)
    return float(src_x), float(src_z), -float(src_y)

def parse_plane(plane_str):
    coords = re.findall(r'\(([^)]+)\)', plane_str)
    if len(coords) < 3: return None
    pts = []
    for c in coords[:3]:
        vals = c.strip().split()
        if len(vals) == 3:
            pts.append((float(vals[0]), float(vals[1]), float(vals[2])))
    return pts

def core_convert(vmf_path, output_path, log_callback):
    log_callback(f"正在读取: {os.path.basename(vmf_path)}")
    try:
        with open(vmf_path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except Exception as e:
        log_callback(f"读取失败: {e}")
        return False
        
    parsed = parse_vmf(text)
    world = parsed.get("world", {})
    if isinstance(world, list):
        world = world[0] if world else {}
    
    blocks = []
    entities = []
    
    # 几何体
    solids = world.get("solid", [])
    if isinstance(solids, dict): solids = [solids]
    
    for solid in solids:
        sides = solid.get("side", [])
        if isinstance(sides, dict): sides = [sides]
        if not sides: continue
            
        raw_mat = sides[0].get("material", "dev")
        mat_name = os.path.basename(raw_mat).lower()
        tex_name = f"{mat_name}.png" if not mat_name.endswith(".png") else mat_name
        
        min_x = min_y = min_z = 999999.0
        max_x = max_y = max_z = -999999.0
        
        for side in sides:
            plane = side.get("plane")
            if not plane: continue
            pts = parse_plane(plane)
            if not pts: continue
            
            for p in pts:
                cx, cy, cz = convert_coord(p[0], p[1], p[2])
                min_x, max_x = min(min_x, cx), max(max_x, cx)
                min_y, max_y = min(min_y, cy), max(max_y, cy)
                min_z, max_z = min(min_z, cz), max(max_z, cz)
                
        if min_x > max_x: continue
        
        center = ((min_x + max_x) / 2.0, (min_y + max_y) / 2.0, (min_z + max_z) / 2.0)
        half = ((max_x - min_x) / 2.0, (max_y - min_y) / 2.0, (max_z - min_z) / 2.0)
        
        half = [max(h, 0.1) for h in half]
        
        blocks.append([
            float(center[0]), float(center[1]), float(center[2]),
            float(half[0]), float(half[1]), float(half[2]),
            tex_name, None, False
        ])

    # 实体
    ents = parsed.get("entity", [])
    if isinstance(ents, dict): ents = [ents]
    
    spawn_x, spawn_z = 0.0, 0.0
    
    for ent in ents:
        classname = ent.get("classname", "")
        if not classname or classname in ("worldspawn", "func_detail"):
            continue
            
        origin_str = ent.get("origin", "0 0 0")
        try:
            ox, oy, oz = map(float, origin_str.split())
        except:
            ox, oy, oz = 0.0, 0.0, 0.0
            
        ex, ey, ez = convert_coord(ox, oy, oz)
        if classname == "info_player_start":
            spawn_x, spawn_z = ex, ez
            
        params = {k: v for k, v in ent.items() if k not in ("classname", "origin", "id")}
        entities.append([classname, float(ex), float(ey), float(ez), None, params])
        
    out_data = {
        "blocks": blocks,
        "entities": entities,
        "spawn": [spawn_x, spawn_z],
        "sky": "day_clear.txt"
    }
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(out_data, f, indent=2)
        
    log_callback(f"成功 -> {os.path.basename(output_path)} (方块:{len(blocks)}, 实体:{len(entities)})")
    return True


# ============================================================
# GUI 界面 (宽扁版，完全模仿 BSPSource)
# ============================================================
class VMFConverterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("VMF to HMMAP Converter (BSPSource Style)")
        # ★ 修改为宽扁比例：宽 700，高 400
        self.root.geometry("700x400") 
        self.root.minsize(600, 350)
        
        style = ttk.Style()
        style.theme_use('clam')
        
        # 1. 顶部 Tab 栏 (占位)
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=(5, 0))
        
        self.tab_files = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_files, text="  Files  ")
        # 假装有其他 Tab
        self.notebook.add(ttk.Frame(self.notebook), text="  World  ")
        self.notebook.add(ttk.Frame(self.notebook), text="  Entities  ")
        self.notebook.add(ttk.Frame(self.notebook), text="  Textures  ")
        self.notebook.add(ttk.Frame(self.notebook), text="  Other  ")
        
        # 2. 主内容区 (左侧列表 + 右侧按钮)
        main_content = ttk.Frame(self.tab_files)
        main_content.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # 左侧：文件列表
        list_frame = ttk.Frame(main_content)
        list_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        self.file_listbox = tk.Listbox(list_frame, selectmode=tk.EXTENDED, bg="white", relief=tk.SUNKEN, bd=1)
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.file_listbox.yview)
        self.file_listbox.config(yscrollcommand=scrollbar.set)
        
        self.file_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 右侧：垂直排列的按钮
        btn_frame = ttk.Frame(main_content)
        btn_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        
        self.btn_add = ttk.Button(btn_frame, text="Add", command=self.add_files)
        self.btn_remove = ttk.Button(btn_frame, text="Remove", command=self.remove_selected)
        self.btn_remove_all = ttk.Button(btn_frame, text="Remove all", command=self.remove_all)
        
        self.btn_add.pack(side=tk.TOP, fill=tk.X, pady=(0, 5))
        self.btn_remove.pack(side=tk.TOP, fill=tk.X, pady=5)
        self.btn_remove_all.pack(side=tk.TOP, fill=tk.X, pady=5)
        
        # 3. 提示文字
        tip_label = tk.Label(self.root, text="Tip: 点击 Add 添加文件 (支持多选)，点击底部 Convert 转换", fg="gray")
        tip_label.pack(side=tk.TOP, anchor=tk.W, padx=10, pady=(0, 5))
        
        # 4. 底部操作栏
        bottom_frame = ttk.Frame(self.root)
        bottom_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=10)
        
        # 左侧：模拟的设置按钮
        self.dark_var = tk.BooleanVar()
        chk_dark = ttk.Checkbutton(bottom_frame, text="Dark theme", variable=self.dark_var)
        chk_dark.pack(side=tk.LEFT)
        
        # 中间：状态提示
        self.log_var = tk.StringVar(value="准备就绪")
        self.status_label = tk.Label(bottom_frame, textvariable=self.log_var, fg="#333")
        self.status_label.pack(side=tk.LEFT, padx=20, expand=True)
        
        # 右侧：转换按钮
        self.btn_convert = ttk.Button(bottom_frame, text="Convert", command=self.start_conversion)
        self.btn_convert.pack(side=tk.RIGHT)
        
        self.files = []

    def add_files(self):
        filetypes = (("Valve Map Format", "*.vmf"), ("All files", "*.*"))
        paths = filedialog.askopenfilenames(title="选择 VMF 文件", filetypes=filetypes)
        for p in paths:
            if p and p not in self.files:
                self.files.append(p)
                self.file_listbox.insert(tk.END, p)

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
            messagebox.showwarning("提示", "请先添加 VMF 文件！")
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
        errors = []  # 新增：用于记录错误
        
        for i, vmf_file in enumerate(self.files):
            self.log(f"进度 [{i+1}/{total}] 正在转换...")
            out_file = str(Path(vmf_file).with_suffix(".hmmap"))
            
            try:
                if core_convert(vmf_file, out_file, self.log):
                    success += 1
            except Exception as e:
                err_msg = f"{os.path.basename(vmf_file)}: {e}"
                errors.append(err_msg)
                self.log(f"错误: {e}")
                
        self.log(f"转换完成! 成功: {success}/{total}")
        self.root.after(0, self.reset_ui)
        
        def show_done():
            msg = f"共处理 {total} 个文件\n成功生成 {success} 个 .hmmap 文件。"
            if errors:
                msg += "\n\n失败原因:\n" + "\n".join(errors[:5]) # 最多显示前5个错误
                if len(errors) > 5:
                    msg += "\n..."
            messagebox.showinfo("完成", msg)
        self.root.after(0, show_done)

    def reset_ui(self):
        self.btn_convert.config(state=tk.NORMAL)
        self.btn_add.config(state=tk.NORMAL)
        self.btn_remove.config(state=tk.NORMAL)
        self.btn_remove_all.config(state=tk.NORMAL)

if __name__ == "__main__":
    root = tk.Tk()
    app = VMFConverterApp(root)
    root.mainloop()