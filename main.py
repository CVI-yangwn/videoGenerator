import argparse
import base64
import http.client
import json
import mimetypes
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request

import cv2

DEFAULT_PROMPT = """15 秒抖音视频提示词
主题：白色灯芯绒短袖衬衫 + 短裤套装（保留服装原有版型）
人物：美国男模特（风格休闲，肢体语言自然）
场景：户外城市街道（天气晴朗，自然光柔和，背景人流稀少）
镜头运镜：
0-5 秒：跟拍镜头（仅正面 / 侧面角度，无背面视角）—— 模特缓步行走
5-10 秒：特写镜头（1-2 秒）—— 聚焦灯芯绒面料质感（模糊画面中所有标识）
10-15 秒：全景镜头 —— 模特移出画面，以套装整体展示收尾
风格：写实风 "街拍"（轻微动态模糊，暖色调，1080p 60 帧）
音频 / 字幕：无旁白，无屏幕文字（搭配热门低保真背景音乐）"""
DEFAULT_CONTINUATION_SUFFIX = "请无缝承接上一段末帧继续拍摄，主体、服饰、场景保持一致，动作自然延续。"

API_KEY = os.environ.get("YUNWU_API_KEY", "sk-6eTfpGREZxdDMHnZEKoIWIPxoF0copuZml85JAYSJaqx64Zs")
HOST = os.environ.get("YUNWU_HOST", "yunwu.ai")


def image_to_data_url(path):
    mime, _ = mimetypes.guess_type(path)
    if mime is None:
        mime = "image/jpeg"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def build_payload(prompt, model, images, enhance_prompt=True, enable_upsample=True, aspect_ratio="9:16"):
    return {
        "prompt": prompt,
        "model": model,
        "images": images,
        "enhance_prompt": enhance_prompt,
        "enable_upsample": enable_upsample,
        "aspect_ratio": aspect_ratio,
    }


def create_video(payload):
    conn = http.client.HTTPSConnection(HOST)
    body = json.dumps(payload)
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    conn.request("POST", "/v1/video/create", body, headers)
    res = conn.getresponse()
    data = json.loads(res.read().decode("utf-8"))
    print("创建任务响应:", json.dumps(data, ensure_ascii=False, indent=2))
    return data


def query_video(task_id):
    conn = http.client.HTTPSConnection(HOST)
    headers = {"Accept": "application/json", "Authorization": f"Bearer {API_KEY}"}
    conn.request("GET", f"/v1/video/query?id={task_id}", headers=headers)
    res = conn.getresponse()
    return json.loads(res.read().decode("utf-8"))


def pick_video_url(result):
    return result.get("video_url") or result.get("url") or result.get("output", {}).get("video_url")


def wait_for_video(task_id, label="视频", poll_interval=1):
    while True:
        time.sleep(poll_interval)
        result = query_video(task_id)
        status = result.get("status", "").lower()
        print(f"[{label}] 当前状态: {status}")

        if status in ("succeeded", "completed", "success"):
            video_url = pick_video_url(result)
            print(f"[{label}] 完成！视频地址: {video_url}")
            return video_url
        if status in ("failed", "error"):
            print(f"[{label}] 失败！{json.dumps(result, ensure_ascii=False)}")
            return None


def download_video(video_url, target_path):
    if not video_url:
        return False
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    with urllib.request.urlopen(video_url, timeout=120) as src:
        with open(target_path, "wb") as out:
            out.write(src.read())
    return True


def extract_last_frame(video_path, frame_path):
    os.makedirs(os.path.dirname(frame_path), exist_ok=True)
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频文件: {video_path}")

    last_frame = None
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        last_frame = frame
    cap.release()

    if last_frame is None:
        raise RuntimeError(f"视频中没有可用帧: {video_path}")

    if not cv2.imwrite(frame_path, last_frame):
        raise RuntimeError(f"写入末帧失败: {frame_path}")
    return frame_path


def merge_videos(video_paths, output_path):
    output_dir = os.path.dirname(output_path) or "."
    os.makedirs(output_dir, exist_ok=True)
    if not video_paths:
        raise ValueError("video_paths 不能为空")
    for path in video_paths:
        if not os.path.exists(path):
            raise FileNotFoundError(f"视频文件不存在: {path}")

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        first_cap = cv2.VideoCapture(video_paths[0])
        if not first_cap.isOpened():
            raise RuntimeError(f"无法打开视频文件: {video_paths[0]}")
        width = int(first_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(first_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        first_cap.release()
        if width <= 0 or height <= 0:
            width, height = 720, 1280

        fps = 30
        normalized_paths = []
        list_file = None
        try:
            # 先标准化每段视频，避免源视频参数不一致导致拼接后异常
            for idx, path in enumerate(video_paths):
                normalized_path = os.path.join(output_dir, f".norm_{idx}.mp4")
                cmd_norm = [
                    ffmpeg,
                    "-y",
                    "-i",
                    path,
                    "-vf",
                    (
                        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
                        "setsar=1,"
                        f"fps={fps}"
                    ),
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-an",
                    normalized_path,
                ]
                norm_ret = subprocess.run(cmd_norm, capture_output=True, text=True)
                if norm_ret.returncode != 0:
                    raise RuntimeError(
                        "ffmpeg 标准化失败: "
                        f"{path}\n{(norm_ret.stderr or norm_ret.stdout)[-1000:]}"
                    )
                normalized_paths.append(normalized_path)

            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
                list_file = f.name
                for path in normalized_paths:
                    abs_path = os.path.abspath(path).replace("\\", "/")
                    escaped = abs_path.replace("'", "'\\''")
                    f.write(f"file '{escaped}'\n")

            cmd_concat = [
                ffmpeg,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                list_file,
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                "-an",
                output_path,
            ]
            concat_ret = subprocess.run(cmd_concat, capture_output=True, text=True)
            if concat_ret.returncode == 0:
                return output_path

            raise RuntimeError(f"ffmpeg 拼接失败: {(concat_ret.stderr or concat_ret.stdout)[-1000:]}")
        except Exception as e:
            print(f"ffmpeg 合并失败，回退到 OpenCV。原因: {e}")
        finally:
            if list_file and os.path.exists(list_file):
                os.remove(list_file)
            for path in normalized_paths:
                if os.path.exists(path):
                    os.remove(path)

    # OpenCV 兜底方案（无 ffmpeg 时）
    first_cap = cv2.VideoCapture(video_paths[0])
    if not first_cap.isOpened():
        raise RuntimeError(f"无法打开视频文件: {video_paths[0]}")
    fps = first_cap.get(cv2.CAP_PROP_FPS)
    width = int(first_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(first_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    first_cap.release()

    if fps <= 0:
        fps = 30.0
    if width <= 0 or height <= 0:
        raise RuntimeError("无法读取输出视频尺寸")

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(output_path, fourcc, fps, (width, height))
    if not writer.isOpened():
        raise RuntimeError(f"无法创建输出视频: {output_path}")

    try:
        for path in video_paths:
            cap = cv2.VideoCapture(path)
            if not cap.isOpened():
                raise RuntimeError(f"无法打开视频文件: {path}")
            try:
                while True:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    if frame.shape[1] != width or frame.shape[0] != height:
                        frame = cv2.resize(frame, (width, height))
                    writer.write(frame)
            finally:
                cap.release()
    finally:
        writer.release()
    return output_path


def run_single_flow(payload, retries_seg1=0):
    for attempt in range(retries_seg1 + 1):
        if attempt > 0:
            print(f"[segment1] 第 {attempt} 次重试")
        created = create_video(payload)
        task_id = created.get("task_id") or created.get("id")
        if not task_id:
            print("未获取到 task_id，请检查创建响应")
            continue
        print(f"[segment1] 任务 ID: {task_id}")
        url = wait_for_video(task_id, label="segment1")
        if url:
            return url
    return None


def run_continuation_flow(
    base_payload,
    continuation_suffix,
    retries_seg1=0,
    retries_seg2=1,
    output_dir="./outputs",
):
    os.makedirs(output_dir, exist_ok=True)
    ctx = {
        "segment1": {},
        "segment2": {},
        "state": "segment1_creating",
        "finalVideoPath": None,
        "retries": 0,
    }

    seg1_url = None
    for attempt in range(retries_seg1 + 1):
        if attempt > 0:
            print(f"[segment1] 第 {attempt} 次重试")
        first_created = create_video(base_payload)
        seg1_task_id = first_created.get("task_id") or first_created.get("id")
        if not seg1_task_id:
            print("segment1 创建失败：无 task_id")
            continue

        ctx["segment1"]["taskId"] = seg1_task_id
        print(f"[segment1] 任务 ID: {seg1_task_id}")
        seg1_url = wait_for_video(seg1_task_id, label="segment1")
        if seg1_url:
            break
        ctx["retries"] = attempt + 1

    if not seg1_url:
        ctx["state"] = "failed"
        return None, ctx

    seg1_path = os.path.join(output_dir, "segment1.mp4")
    download_video(seg1_url, seg1_path)
    frame_path = os.path.join(output_dir, "segment1_last.jpg")
    extract_last_frame(seg1_path, frame_path)
    ctx["segment1"].update({"videoUrl": seg1_url, "localPath": seg1_path, "lastFramePath": frame_path})
    ctx["state"] = "segment1_done"

    seg2_payload = dict(base_payload)
    seg2_payload["prompt"] = f"{base_payload['prompt']}\n\n{continuation_suffix}"
    seg2_payload["images"] = [image_to_data_url(frame_path)]

    ctx["state"] = "segment2_creating"
    seg2_url = None
    for attempt in range(retries_seg2 + 1):
        if attempt > 0:
            print(f"[segment2] 第 {attempt} 次重试")
        seg2_created = create_video(seg2_payload)
        seg2_task_id = seg2_created.get("task_id") or seg2_created.get("id")
        if not seg2_task_id:
            continue
        ctx["segment2"]["taskId"] = seg2_task_id
        seg2_url = wait_for_video(seg2_task_id, label="segment2")
        if seg2_url:
            break
        ctx["retries"] = attempt + 1

    if not seg2_url:
        ctx["state"] = "failed"
        return None, ctx

    seg2_path = os.path.join(output_dir, "segment2.mp4")
    download_video(seg2_url, seg2_path)
    ctx["segment2"].update({"videoUrl": seg2_url, "localPath": seg2_path})
    ctx["state"] = "segment2_done"

    ctx["state"] = "merging"
    final_path = os.path.join(output_dir, "final_continuation.mp4")
    merge_videos([seg1_path, seg2_path], final_path)
    ctx["finalVideoPath"] = final_path
    ctx["state"] = "done"
    return final_path, ctx


def parse_args():
    parser = argparse.ArgumentParser(description="yunwu 视频生成（支持续接）")
    parser.add_argument("--ui", default=True, help="启动桌面界面")
    parser.add_argument("--mode", choices=["single", "continuation"], default="continuation")
    parser.add_argument("--image", default="./4befb8bc9f787d21071022fac2a3baf5.jpg")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--model", default="veo_3_1-lite")
    parser.add_argument("--aspect-ratio", default="9:16")
    parser.add_argument("--continuation-suffix", default=DEFAULT_CONTINUATION_SUFFIX)
    parser.add_argument("--seg1-retries", type=int, default=1)
    parser.add_argument("--seg2-retries", type=int, default=1)
    parser.add_argument("--output-dir", default="./outputs")
    return parser.parse_args()


def run_generation(args):
    if not args.image or not os.path.exists(args.image):
        raise FileNotFoundError(f"参考图片不存在: {args.image}")
    payload = build_payload(
        prompt=args.prompt,
        model=args.model,
        images=[image_to_data_url(args.image)],
        enhance_prompt=True,
        enable_upsample=True,
        aspect_ratio=args.aspect_ratio,
    )

    if args.mode == "single":
        print("=== 单段生成 ===")
        url = run_single_flow(payload, retries_seg1=max(0, args.seg1_retries))
        print(f"结果 URL: {url}")
        return {"state": "done" if url else "failed", "url": url, "path": None, "ctx": None}

    print("=== 续接生成（2x8s）===")
    final_path, ctx = run_continuation_flow(
        base_payload=payload,
        continuation_suffix=args.continuation_suffix,
        retries_seg1=max(0, args.seg1_retries),
        retries_seg2=max(0, args.seg2_retries),
        output_dir=args.output_dir,
    )
    print("流程状态:", ctx["state"])
    if final_path:
        print("拼接完成:", final_path)
    else:
        print("续接失败，阶段信息:", json.dumps(ctx, ensure_ascii=False, indent=2))
    return {"state": ctx["state"], "url": None, "path": final_path, "ctx": ctx}


def launch_ui():
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except ImportError:
        print("当前 Python 环境不可用 tkinter，无法启动桌面界面。")
        return

    root = tk.Tk()
    root.title("Video Generator")
    root.geometry("860x760")

    mode_var = tk.StringVar(value="continuation")
    image_var = tk.StringVar(value="./4befb8bc9f787d21071022fac2a3baf5.jpg")
    model_var = tk.StringVar(value="veo_3_1-lite")
    aspect_var = tk.StringVar(value="9:16")
    output_var = tk.StringVar(value="./outputs")
    seg1_var = tk.StringVar(value="1")
    seg2_var = tk.StringVar(value="1")
    host_var = tk.StringVar(value=HOST)
    api_key_var = tk.StringVar(value=API_KEY if API_KEY != "YOUR_TOKEN" else "")
    continuation_suffix_var = tk.StringVar(value=DEFAULT_CONTINUATION_SUFFIX)

    ttk.Label(root, text="运行模式").grid(row=0, column=0, sticky="w", padx=10, pady=6)
    mode_box = ttk.Combobox(root, textvariable=mode_var, values=["single", "continuation"], state="readonly")
    mode_box.grid(row=0, column=1, sticky="ew", padx=10, pady=6)

    ttk.Label(root, text="参考图片").grid(row=1, column=0, sticky="w", padx=10, pady=6)
    image_entry = ttk.Entry(root, textvariable=image_var)
    image_entry.grid(row=1, column=1, sticky="ew", padx=10, pady=6)

    def choose_image():
        selected = filedialog.askopenfilename(
            title="选择参考图片",
            filetypes=[("Image Files", "*.jpg *.jpeg *.png *.webp *.bmp"), ("All Files", "*.*")],
        )
        if selected:
            image_var.set(selected)

    ttk.Button(root, text="浏览", command=choose_image).grid(row=1, column=2, sticky="ew", padx=10, pady=6)

    ttk.Label(root, text="模型").grid(row=2, column=0, sticky="w", padx=10, pady=6)
    ttk.Entry(root, textvariable=model_var).grid(row=2, column=1, sticky="ew", padx=10, pady=6)

    ttk.Label(root, text="宽高比").grid(row=3, column=0, sticky="w", padx=10, pady=6)
    ttk.Entry(root, textvariable=aspect_var).grid(row=3, column=1, sticky="ew", padx=10, pady=6)

    ttk.Label(root, text="segment1 最大重试").grid(row=4, column=0, sticky="w", padx=10, pady=6)
    ttk.Entry(root, textvariable=seg1_var).grid(row=4, column=1, sticky="ew", padx=10, pady=6)

    ttk.Label(root, text="segment2 最大重试").grid(row=5, column=0, sticky="w", padx=10, pady=6)
    ttk.Entry(root, textvariable=seg2_var).grid(row=5, column=1, sticky="ew", padx=10, pady=6)

    ttk.Label(root, text="输出目录").grid(row=6, column=0, sticky="w", padx=10, pady=6)
    ttk.Entry(root, textvariable=output_var).grid(row=6, column=1, sticky="ew", padx=10, pady=6)

    def choose_output_dir():
        selected = filedialog.askdirectory(title="选择输出目录")
        if selected:
            output_var.set(selected)

    ttk.Button(root, text="选择目录", command=choose_output_dir).grid(row=6, column=2, sticky="ew", padx=10, pady=6)

    ttk.Label(root, text="API Host").grid(row=7, column=0, sticky="w", padx=10, pady=6)
    ttk.Entry(root, textvariable=host_var).grid(row=7, column=1, sticky="ew", padx=10, pady=6)

    ttk.Label(root, text="API Key").grid(row=8, column=0, sticky="w", padx=10, pady=6)
    ttk.Entry(root, textvariable=api_key_var, show="*").grid(row=8, column=1, sticky="ew", padx=10, pady=6)

    ttk.Label(root, text="Prompt").grid(row=9, column=0, sticky="nw", padx=10, pady=6)
    prompt_text = tk.Text(root, height=10, wrap="word")
    prompt_text.grid(row=9, column=1, columnspan=2, sticky="nsew", padx=10, pady=6)
    prompt_text.insert("1.0", DEFAULT_PROMPT)

    ttk.Label(root, text="续接补充提示词").grid(row=10, column=0, sticky="nw", padx=10, pady=6)
    suffix_entry = ttk.Entry(root, textvariable=continuation_suffix_var)
    suffix_entry.grid(row=10, column=1, columnspan=2, sticky="ew", padx=10, pady=6)

    ttk.Label(root, text="运行日志").grid(row=11, column=0, sticky="nw", padx=10, pady=6)
    log_box = tk.Text(root, height=12, wrap="word", state="disabled")
    log_box.grid(row=11, column=1, columnspan=2, sticky="nsew", padx=10, pady=6)

    for col in (1, 2):
        root.grid_columnconfigure(col, weight=1)
    root.grid_rowconfigure(9, weight=1)
    root.grid_rowconfigure(11, weight=1)

    def append_log(text):
        log_box.configure(state="normal")
        log_box.insert("end", f"{text}\n")
        log_box.see("end")
        log_box.configure(state="disabled")

    old_print = print

    def run_in_thread(start_button, args):
        global HOST, API_KEY
        HOST = host_var.get().strip() or HOST
        API_KEY = api_key_var.get().strip() or API_KEY
        if not API_KEY or API_KEY == "YOUR_TOKEN":
            root.after(0, messagebox.showerror, "缺少密钥", "请在界面中填写 API Key")
            root.after(0, lambda: start_button.configure(state="normal"))
            return

        def ui_print(*parts, **kwargs):
            text = " ".join(str(p) for p in parts)
            root.after(0, append_log, text)
            old_print(*parts, **kwargs)

        try:
            globals()["print"] = ui_print
            result = run_generation(args)
            if result["state"] == "done":
                root.after(0, messagebox.showinfo, "完成", f"任务完成。\n结果: {result.get('path') or result.get('url')}")
            else:
                root.after(0, messagebox.showwarning, "失败", "任务执行失败，请查看日志")
        except Exception as e:
            root.after(0, append_log, f"异常: {e}")
            root.after(0, messagebox.showerror, "执行异常", str(e))
        finally:
            globals()["print"] = old_print
            root.after(0, lambda: start_button.configure(state="normal"))

    def start_run():
        try:
            seg1_retries = max(0, int(seg1_var.get().strip()))
            seg2_retries = max(0, int(seg2_var.get().strip()))
        except ValueError:
            messagebox.showerror("参数错误", "segment 重试次数必须是非负整数")
            return
        args = argparse.Namespace(
            ui=False,
            mode=mode_var.get().strip() or "continuation",
            image=image_var.get().strip(),
            prompt=prompt_text.get("1.0", "end").strip(),
            model=model_var.get().strip() or "veo_3_1-lite",
            aspect_ratio=aspect_var.get().strip() or "9:16",
            continuation_suffix=continuation_suffix_var.get().strip(),
            seg1_retries=seg1_retries,
            seg2_retries=seg2_retries,
            output_dir=output_var.get().strip() or "./outputs",
        )
        start_button.configure(state="disabled")
        t = threading.Thread(target=run_in_thread, args=(start_button, args), daemon=True)
        t.start()

    def clear_log():
        log_box.configure(state="normal")
        log_box.delete("1.0", "end")
        log_box.configure(state="disabled")

    btn_frame = ttk.Frame(root)
    btn_frame.grid(row=12, column=1, columnspan=2, sticky="e", padx=10, pady=10)
    ttk.Button(btn_frame, text="清空日志", command=clear_log).pack(side="left", padx=6)
    start_button = ttk.Button(btn_frame, text="开始生成", command=start_run)
    start_button.pack(side="left", padx=6)

    root.mainloop()


def main():
    args = parse_args()
    if args.ui:
        launch_ui()
        return
    if API_KEY == "YOUR_TOKEN":
        print("请设置环境变量 YUNWU_API_KEY 后再运行")
        return
    run_generation(args)


if __name__ == "__main__":
    main()
