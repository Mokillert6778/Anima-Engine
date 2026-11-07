import tkinter as tk
import ctypes
import subprocess
import sys
import os

# 修复高分屏模糊
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

class AnimaSDKApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Anima SDK")
        self.root.geometry("315x474")
        self.root.resizable(False, False)
        
        # 砍掉系统自带的白色标题栏
        self.root.overrideredirect(True)
        
        # 获取当前脚本所在目录（确保能正确找到 Hammer.py）
        self.current_dir = os.path.dirname(os.path.abspath(__file__))
        
        # ================= 纯黑主题配色 =================
        self.bg_color = "#000000"        # 纯黑背景
        self.text_color = "#FFFFFF"      # 纯白文字
        self.combo_bg = "#1A1A1A"        # 下拉框深灰底
        self.btn_bg = "#1A1A1A"          # 按钮深灰底
        self.border_color = "#333333"    # 窗口边缘的深灰细线
        
        # 整个窗口的主容器
        self.main_container = tk.Frame(root, bg=self.bg_color, highlightbackground=self.border_color, highlightthickness=1)
        self.main_container.pack(fill=tk.BOTH, expand=True)
        
        # 统一字体大小
        font_title = ("Microsoft YaHei", 10, "bold")
        font_unified = ("Microsoft YaHei", 9)
        font_unified_bold = ("Microsoft YaHei", 9, "bold")
        
        # ================= 自定义纯黑标题栏 =================
        self.title_bar = tk.Frame(self.main_container, bg=self.bg_color, height=30)
        self.title_bar.pack(fill=tk.X, side=tk.TOP)
        self.title_bar.pack_propagate(False) 
        
        title_lbl = tk.Label(self.title_bar, text="🪶 Anima SDK", fg=self.text_color, bg=self.bg_color, font=font_title)
        title_lbl.pack(side=tk.LEFT, padx=10)
        
        close_btn = tk.Label(self.title_bar, text="✕", fg="#AAAAAA", bg=self.bg_color, font=("Arial", 10), cursor="hand2")
        close_btn.pack(side=tk.RIGHT, padx=10)
        close_btn.bind("<Button-1>", lambda e: self.root.destroy())
        close_btn.bind("<Enter>", lambda e: close_btn.configure(fg="#FF5555"))
        close_btn.bind("<Leave>", lambda e: close_btn.configure(fg="#AAAAAA"))

        self.title_bar.bind("<Button-1>", self.start_move)
        self.title_bar.bind("<B1-Motion>", self.do_move)
        title_lbl.bind("<Button-1>", self.start_move)
        title_lbl.bind("<B1-Motion>", self.do_move)

        # ================= 主内容区 =================
        main_frame = tk.Frame(self.main_container, bg=self.bg_color)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        def add_header(text):
            lbl = tk.Label(main_frame, text=text, fg=self.text_color, bg=self.bg_color, 
                           font=font_unified_bold, anchor="w", padx=15)
            lbl.pack(fill=tk.X, pady=(15, 5))

        def add_item(icon, text, command=None):
            item_frame = tk.Frame(main_frame, bg=self.bg_color)
            item_frame.pack(fill=tk.X, padx=15, pady=3)
            
            icon_lbl = tk.Label(item_frame, text=icon, fg=self.text_color, bg=self.bg_color, 
                                font=font_unified, width=2)
            icon_lbl.pack(side=tk.LEFT)
            
            text_lbl = tk.Label(item_frame, text=text, fg=self.text_color, bg=self.bg_color, 
                                font=font_unified, cursor="hand2" if command else "arrow")
            text_lbl.pack(side=tk.LEFT)
            
            # 悬停变色
            text_lbl.bind("<Enter>", lambda e, lbl=text_lbl: lbl.configure(fg="#64B5F6") if command else None)
            text_lbl.bind("<Leave>", lambda e, lbl=text_lbl: lbl.configure(fg=self.text_color))
            
            # 点击事件
            if command:
                text_lbl.bind("<Button-1>", lambda e, cmd=command: self.run_script(cmd))
                icon_lbl.bind("<Button-1>", lambda e, cmd=command: self.run_script(cmd))
                item_frame.bind("<Button-1>", lambda e, cmd=command: self.run_script(cmd))

        # ================= 填充内容 =================
        add_header("APPLICATIONS")
        
        # 绑定 Hammer 和 Model Viewer 的启动事件
        add_item("⚒", "Hammer Editor", command="Hammer.py")
        add_item("💠", "Model Viewer", command="Hlmv.py")
        add_item("👤", "Face Poser")  # 暂时没绑定逻辑

        # ================= 底部控制区 =================
        bottom_frame = tk.Frame(self.main_container, bg=self.bg_color)
        bottom_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=(0, 10))

        # Engine Version 行
        row1 = tk.Frame(bottom_frame, bg=self.bg_color)
        row1.pack(fill=tk.X, pady=3)
        tk.Label(row1, text="Engine Version:", fg=self.text_color, bg=self.bg_color, font=font_unified).pack(side=tk.LEFT)
        
        combo1 = tk.Frame(row1, bg=self.combo_bg, highlightbackground=self.border_color, highlightthickness=1)
        combo1.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(8, 0))
        tk.Label(combo1, text="Anima Engine 2026", fg=self.text_color, bg=self.combo_bg, 
                 font=font_unified, anchor="w", padx=5).pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(combo1, text="▼", fg="#888888", bg=self.combo_bg, font=("Arial", 6)).pack(side=tk.RIGHT, padx=5)

        # Current Game 行
        row2 = tk.Frame(bottom_frame, bg=self.bg_color)
        row2.pack(fill=tk.X, pady=3)
        tk.Label(row2, text="Current Game:", fg=self.text_color, bg=self.bg_color, font=font_unified).pack(side=tk.LEFT)
        
        combo2 = tk.Frame(row2, bg=self.combo_bg, highlightbackground=self.border_color, highlightthickness=1)
        combo2.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(17, 0))
        tk.Label(combo2, text="54", fg=self.text_color, bg=self.combo_bg, 
                 font=font_unified, anchor="w", padx=5).pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(combo2, text="▼", fg="#888888", bg=self.combo_bg, font=("Arial", 6)).pack(side=tk.RIGHT, padx=5)

        # Close 按钮
        row3 = tk.Frame(bottom_frame, bg=self.bg_color)
        row3.pack(fill=tk.X, pady=(10, 0))
        
        btn_close = tk.Button(row3, text="Close", font=font_unified, bg=self.btn_bg, fg=self.text_color, 
                              relief="flat", bd=0, padx=20, activebackground="#333333", activeforeground=self.text_color,
                              highlightbackground=self.border_color, highlightthickness=1, command=self.root.destroy)
        btn_close.pack(side=tk.RIGHT)
        
    # ================= 启动外部 Python 脚本 =================
    def run_script(self, script_name):
        script_path = os.path.join(self.current_dir, script_name)
        if not os.path.exists(script_path):
            print(f"错误：找不到 {script_path}")
            return
        
        try:
            # 使用 sys.executable 保证用当前的 Python 解释器启动
            # cwd 设置为 current_dir 保证相对路径资源（如 materials）读取正确
            subprocess.Popen([sys.executable, script_path], cwd=self.current_dir)
        except Exception as e:
            print(f"启动 {script_name} 失败: {e}")

    # ================= 窗口拖拽功能 =================
    def start_move(self, event):
        self.x = event.x
        self.y = event.y

    def do_move(self, event):
        deltax = event.x - self.x
        deltay = event.y - self.y
        x = self.root.winfo_x() + deltax
        y = self.root.winfo_y() + deltay
        self.root.geometry(f"+{x}+{y}")

if __name__ == "__main__":
    root = tk.Tk()
    app = AnimaSDKApp(root)
    root.mainloop()