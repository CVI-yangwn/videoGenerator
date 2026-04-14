import argparse
import base64
import http.client
import json
import mimetypes
import os
import subprocess
import time
import urllib.request

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
    cmd = [
        "ffmpeg",
        "-y",
        "-sseof",
        "-0.08",
        "-i",
        video_path,
        "-vframes",
        "1",
        frame_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return frame_path


def merge_videos(video_paths, output_path):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    concat_list = os.path.join(os.path.dirname(output_path), "concat_inputs.txt")
    with open(concat_list, "w", encoding="utf-8") as f:
        for p in video_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")

    copy_cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        concat_list,
        "-c",
        "copy",
        output_path,
    ]
    reencode_cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        concat_list,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        output_path,
    ]
    try:
        subprocess.run(copy_cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError:
        subprocess.run(reencode_cmd, check=True, capture_output=True, text=True)
    return output_path


def run_single_flow(payload):
    created = create_video(payload)
    task_id = created.get("task_id") or created.get("id")
    if not task_id:
        print("未获取到 task_id，请检查创建响应")
        return None
    print(f"[segment1] 任务 ID: {task_id}")
    return wait_for_video(task_id, label="segment1")


def run_continuation_flow(base_payload, continuation_suffix, retries_seg2=1, output_dir="./outputs"):
    os.makedirs(output_dir, exist_ok=True)
    ctx = {
        "segment1": {},
        "segment2": {},
        "state": "segment1_creating",
        "finalVideoPath": None,
        "retries": 0,
    }

    first_created = create_video(base_payload)
    seg1_task_id = first_created.get("task_id") or first_created.get("id")
    if not seg1_task_id:
        print("segment1 创建失败：无 task_id")
        ctx["state"] = "failed"
        return None, ctx

    ctx["segment1"]["taskId"] = seg1_task_id
    print(f"[segment1] 任务 ID: {seg1_task_id}")
    seg1_url = wait_for_video(seg1_task_id, label="segment1")
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
    parser.add_argument("--mode", choices=["single", "continuation"], default="continuation")
    parser.add_argument("--image", default="./4befb8bc9f787d21071022fac2a3baf5.jpg")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--model", default="veo3-fast-frames")
    parser.add_argument("--aspect-ratio", default="9:16")
    parser.add_argument("--continuation-suffix", default=DEFAULT_CONTINUATION_SUFFIX)
    parser.add_argument("--seg2-retries", type=int, default=1)
    parser.add_argument("--output-dir", default="./outputs")
    return parser.parse_args()


def main():
    if API_KEY == "YOUR_TOKEN":
        print("请设置环境变量 YUNWU_API_KEY 后再运行")
        return

    args = parse_args()
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
        url = run_single_flow(payload)
        print(f"结果 URL: {url}")
        return

    print("=== 续接生成（2x8s）===")
    final_path, ctx = run_continuation_flow(
        base_payload=payload,
        continuation_suffix=args.continuation_suffix,
        retries_seg2=max(0, args.seg2_retries),
        output_dir=args.output_dir,
    )
    print("流程状态:", ctx["state"])
    if final_path:
        print("拼接完成:", final_path)
    else:
        print("续接失败，阶段信息:", json.dumps(ctx, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
