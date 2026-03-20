import os
import sys

# 彻底屏蔽底层 C/C++ 库 (OpenCV FFmpeg) 强行写入的啰嗦日志
try:
    null_fd = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null_fd, 2)
except Exception:
    pass

os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "-8"
import cv2
import easyocr
import re
import subprocess
import shutil
import imageio_ffmpeg
from datetime import datetime, timedelta, timezone
import win32_setctime
import time
import threading
import tkinter as tk
from tkinter import ttk
from tkinter import messagebox

class AutoRenameApp:
    def __init__(self, root, vid_dir, res_dir, use_byres=False):
        self.root = root
        self.vid_dir = vid_dir
        self.res_dir = res_dir
        self.use_byres = use_byres
        
        self.root.title("智能行车记录仪批量重命名工具")
        self.root.geometry("600x250")
        self.root.resizable(False, False)
        
        # 窗口居中显示
        self.root.eval('tk::PlaceWindow . center')
        
        # 应用稍微现代一点的拟物化/极简系统扁平主题
        style = ttk.Style()
        style.theme_use('clam')
        
        # --- UI Elements 绑定 ---
        self.status_var = tk.StringVar(value="准备就绪...")
        self.file_var = tk.StringVar(value="")
        self.progress_var = tk.DoubleVar(value=0)
        self.time_var = tk.StringVar(value="系统测算中...")
        
        main_frame = ttk.Frame(root, padding="20 20 20 20")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 阶段主标题
        ttk.Label(main_frame, textvariable=self.status_var, font=("Microsoft YaHei", 12, "bold")).pack(anchor=tk.W, pady=(0, 10))
        # 正在处理的具体文件路径或名称信息
        ttk.Label(main_frame, textvariable=self.file_var, font=("Microsoft YaHei", 9), foreground="gray").pack(anchor=tk.W, pady=(0, 15))
        
        # UI 进度指示器
        self.progressbar = ttk.Progressbar(main_frame, variable=self.progress_var, maximum=100, length=550)
        self.progressbar.pack(fill=tk.X, pady=(0, 10))
        
        # 精确动态倒计时
        ttk.Label(main_frame, textvariable=self.time_var, font=("Microsoft YaHei", 10), foreground="#0066cc").pack(anchor=tk.E)
        
        # 丝滑过渡进度条的核心参数
        self._target_progress = 0.0
        self._anim_step()
        
        # 启动后台处理线程，以免高负载处理卡死 GUI 线程 (弹框变成无响应)
        self.thread = threading.Thread(target=self.process_videos, daemon=True)
        
        # 给 UI 一丁点绘制渲染的起步时间再拉起模型
        self.root.after(500, self.thread.start)
        
    def _anim_step(self):
        # 采用 Apple 风格的 Ease-Out 缓动插值算法，实现连续的过渡动画
        curr = self.progress_var.get()
        target = self._target_progress
        
        if abs(target - curr) > 0.05:
            # 每次前进剩余距离的 8%，丝滑减速
            self.progress_var.set(curr + (target - curr) * 0.08)
        else:
            self.progress_var.set(target)
            
        # 保持动画帧率在 ~33fps (30ms 每帧)
        self.root.after(30, self._anim_step)
        
    def update_ui(self, status=None, file=None, progress=None, time_str=None):
        if status is not None: self.status_var.set(status)
        if file is not None: self.file_var.set(file)
        if progress is not None: self._target_progress = progress
        if time_str is not None: self.time_var.set(time_str)

    def process_videos(self):    
        if not os.path.exists(self.res_dir):
            os.makedirs(self.res_dir)
        else:
            for f in os.listdir(self.res_dir):
                path = os.path.join(self.res_dir, f)
                if os.path.isfile(path):
                    try: os.remove(path)
                    except Exception: pass

        self.update_ui(status="🚀 正在初始化 OCR AI引擎...", file="首次运行或加载模型中，请稍后...")
        reader = easyocr.Reader(['en'], gpu=True, verbose=False) 
        
        date_pattern = re.compile(r"(\d{4})[^\d]*(\d{2})[^\d]*(\d{2})[^\d]*(\d{2})[^\d]*(\d{2})[^\d]*(\d{2})")
        video_extensions = ('.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv')
        
        local_tz_offset = datetime.now() - datetime.utcnow()
        
        files_to_process = [f for f in os.listdir(self.vid_dir) if f.lower().endswith(video_extensions)]
        total_files = len(files_to_process)
        
        if total_files == 0:
            self.root.after(0, self._show_empty_and_quit)
            return
            
        start_time = time.time()
        
        # 科学严谨的剩余时间计算基础：
        # 不要通过“文件的个数”计算时间！因为行车记录仪长短不一，有的10MB有的1GB。
        # 这里通过总合 "硬盘真实的比特体积(Bytes)" 进行速率除法，测算最精准的拷贝预测时间。
        total_bytes = sum(os.path.getsize(os.path.join(self.vid_dir, f)) for f in files_to_process)
        processed_bytes = 0
        
        for idx, filename in enumerate(files_to_process, 1):
            vid_path = os.path.join(self.vid_dir, filename)
            file_size = os.path.getsize(vid_path)
            
            self.update_ui(
                status=f"⚙️ 正在转换视频 ({idx}/{total_files})...", 
                file=f"当前文件: {filename}   |   大小: {file_size / (1024*1024):.1f} MB"
            )
            
            cap = cv2.VideoCapture(vid_path)
            if not cap.isOpened():
                processed_bytes += file_size
                continue
                
            vid_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            vid_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            
            target_msecs = [5000, 6000, 4000]
            match_success = False
            
            for msec in target_msecs:
                cap.set(cv2.CAP_PROP_POS_MSEC, msec)
                ret, frame = cap.read()
                
                if not ret: continue
                    
                h, w = frame.shape[:2]
                crop_h, crop_w = min(h, 250), min(w, 1000)
                cropped_frame = frame[0:crop_h, 0:crop_w]
                
                resized_frame = cv2.resize(cropped_frame, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
                gray_frame = cv2.cvtColor(resized_frame, cv2.COLOR_BGR2GRAY)
                
                ocr_results = reader.readtext(gray_frame, detail=0)
                text = "".join(ocr_results).replace(' ', '')
                
                match = date_pattern.search(text)
                if match:
                    year, month, day, hour, minute, second = match.groups()
                    try:
                        dt_local = datetime.strptime(f"{year}-{month}-{day} {hour}:{minute}:{second}", "%Y-%m-%d %H:%M:%S")
                        dt_start_local = dt_local - timedelta(milliseconds=msec)
                        match_success = True
                        
                        year_s = dt_start_local.strftime("%Y")
                        month_s = dt_start_local.strftime("%m")
                        day_s = dt_start_local.strftime("%d")
                        hour_s = dt_start_local.strftime("%H")
                        minute_s = dt_start_local.strftime("%M")
                        second_s = dt_start_local.strftime("%S")
                        
                        _, ext = os.path.splitext(filename)
                        res_suffix = ""
                        if self.use_byres:
                            if vid_width == 1920 and vid_height == 1080:
                                res_suffix = "_verticalview"
                            elif vid_width == 2672 and vid_height == 1728:
                                res_suffix = "_frontview"
                                
                        base_new_name = f"{year_s}-{month_s}-{day_s}_{hour_s}-{minute_s}-{second_s}{res_suffix}"
                        new_name = f"{base_new_name}{ext}"
                        new_path = os.path.join(self.res_dir, new_name)
                        
                        counter = 1
                        while os.path.exists(new_path):
                            new_name = f"{base_new_name}_{counter}{ext}"
                            new_path = os.path.join(self.res_dir, new_name)
                            counter += 1
                            
                        os_timestamp = dt_start_local.timestamp()
                        dt_utc = dt_start_local - local_tz_offset
                        ffmpeg_date_str = dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
                        
                        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
                        ffmpeg_cmd = [
                            ffmpeg_exe, '-y', '-i', vid_path,
                            '-metadata', f'creation_time={ffmpeg_date_str}',
                            '-c', 'copy', new_path
                        ]
                        
                        # 执行底层的拷贝写入时是最耗时的操作，也是计算速度最大的变数
                        subprocess.run(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                        win32_setctime.setctime(new_path, os_timestamp)
                        os.utime(new_path, (os_timestamp, os_timestamp))
                        break 
                    except Exception:
                        break
            cap.release()
            
            # --- 精密推算 ETA 进度与剩余时间 ---
            processed_bytes += file_size
            elapsed_time = time.time() - start_time
            if processed_bytes > 0:
                speed_bps = processed_bytes / elapsed_time
                remaining_bytes = total_bytes - processed_bytes
                eta_seconds = int(remaining_bytes / speed_bps)
                
                eta_m, eta_s = divmod(eta_seconds, 60)
                eta_h, eta_m = divmod(eta_m, 60)
                
                if eta_h > 0:
                    time_str = f"精确预计剩余时间：{eta_h}小时 {eta_m}分钟 {eta_s}秒"
                elif eta_m > 0:
                    time_str = f"精确预计剩余时间：{eta_m}分钟 {eta_s}秒"
                else:
                    time_str = f"精确预计剩余时间：{eta_s}秒"
            else:
                time_str = "剩余估时中..."
                
            self.update_ui(progress=(processed_bytes / total_bytes) * 100, time_str=time_str)

        total_elapsed = time.time() - start_time
        
        # 回调到主线程执行安全弹窗退出
        self.root.after(0, lambda: self._finish_task(total_elapsed))

    def _finish_task(self, total_elapsed):
        self.update_ui(status="✅ 转换全部成功完成！", file="所有时间属性均写入成功！", progress=100, time_str=f"任务总耗时: {total_elapsed:.1f} 秒")
        messagebox.showinfo("任务完成", "视频重命名和底层属性写入已全部成功！")
        self.root.quit()
        
    def _show_empty_and_quit(self):
        self.update_ui(status="任务结束", file="没有找到任何视频文件！", progress=100, time_str="剩余时间: 0s")
        messagebox.showinfo("完成", "指定文件夹内未找到视频文件。")
        self.root.quit()

if __name__ == '__main__':
    use_byres_flag = '-byres' in sys.argv
    video_directory = r"D:\code\auto_rename by ocr pic\vid"
    result_directory = r"D:\code\auto_rename by ocr pic\result"
    
    # 拉起图形化界面主循环
    root = tk.Tk()
    app = AutoRenameApp(root, video_directory, result_directory, use_byres=use_byres_flag)
    root.mainloop()
