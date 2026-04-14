import base64
import http.client
import json
import mimetypes
import time

API_KEY = "sk-6eTfpGREZxdDMHnZEKoIWIPxoF0copuZml85JAYSJaqx64Zs"
HOST = "yunwu.ai"


def image_to_data_url(path):
    mime, _ = mimetypes.guess_type(path)
    if mime is None:
        mime = "image/jpeg"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def create_video():
    conn = http.client.HTTPSConnection(HOST)
    payload = json.dumps({
        "prompt": """15 秒抖音视频提示词
主题：白色灯芯绒短袖衬衫 + 短裤套装（保留服装原有版型）
人物：美国男模特（风格休闲，肢体语言自然）
场景：户外城市街道（天气晴朗，自然光柔和，背景人流稀少）
镜头运镜：
0-5 秒：跟拍镜头（仅正面 / 侧面角度，无背面视角）—— 模特缓步行走
5-10 秒：特写镜头（1-2 秒）—— 聚焦灯芯绒面料质感（模糊画面中所有标识）
10-15 秒：全景镜头 —— 模特移出画面，以套装整体展示收尾
风格：写实风 "街拍"（轻微动态模糊，暖色调，1080p 60 帧）
音频 / 字幕：无旁白，无屏幕文字（搭配热门低保真背景音乐）""",
        "model": "veo3-fast-frames",
        "images": [
            image_to_data_url(r"./4befb8bc9f787d21071022fac2a3baf5.jpg"),
        ],
        "enhance_prompt": True,
        "enable_upsample": True,
        "aspect_ratio": "9:16"
    })
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    conn.request("POST", "/v1/video/create", payload, headers)
    res = conn.getresponse()
    data = json.loads(res.read().decode("utf-8"))
    print("创建任务响应:", json.dumps(data, ensure_ascii=False, indent=2))
    return data.get("task_id") or data.get("id")


def query_video(task_id):
    conn = http.client.HTTPSConnection(HOST)
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {API_KEY}",
    }
    conn.request("GET", f"/v1/video/query?id={task_id}", headers=headers)
    res = conn.getresponse()
    return json.loads(res.read().decode("utf-8"))


def wait_for_video(task_id, label="视频"):
    poll_interval = 1
    while True:
        time.sleep(poll_interval)
        result = query_video(task_id)
        status = result.get("status", "").lower()
        print(f"[{label}] 当前状态: {status}")

        if status in ("succeeded", "completed", "success"):
            video_url = (
                result.get("video_url")
                or result.get("url")
                or result.get("output", {}).get("video_url")
            )
            print(f"[{label}] 完成！视频地址: {video_url}")
            print("完整响应:", json.dumps(result, ensure_ascii=False, indent=2))
            return video_url
        elif status in ("failed", "error"):
            print(f"[{label}] 失败！")
            print("错误信息:", json.dumps(result, ensure_ascii=False, indent=2))
            return None


def main():
    print("=== 生成视频 ===")
    task_id = create_video()
    if not task_id:
        print("未获取到 task_id，请检查创建响应")
        return

    print(f"任务 ID: {task_id}")
    wait_for_video(task_id, label="视频")


if __name__ == "__main__":
    main()
