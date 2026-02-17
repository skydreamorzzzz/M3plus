# -*- coding: utf-8 -*-
"""
src/llm/wanx_client.py

Wanx image generation / editing client.

Key points (per official docs):
- Task submission MUST include header: X-DashScope-Async: enable
- Beijing and Singapore/intl regions use different endpoints and API keys (do NOT mix).
  If auth fails (401/403), we auto-fallback between dashscope.aliyuncs.com and dashscope-intl.aliyuncs.com.
- Text2Image endpoint:
    POST {base}/api/v1/services/aigc/text2image/image-synthesis
- Image2Image (editing) endpoint:
    POST {base}/api/v1/services/aigc/image2image/image-synthesis
- Poll:
    GET  {base}/api/v1/tasks/{task_id}
"""

from __future__ import annotations

import os
import time
import json
import base64
import mimetypes
from io import BytesIO
from pathlib import Path
from typing import Optional, Dict, Any, List

import requests
from openai import OpenAI


# ============================================================
# Helpers
# ============================================================

def _swap_region_host(base_url: str) -> str:
    """
    Swap between Beijing and intl endpoints.
    """
    b = base_url.rstrip("/")
    if "dashscope-intl.aliyuncs.com" in b:
        return b.replace("dashscope-intl.aliyuncs.com", "dashscope.aliyuncs.com")
    if "dashscope.aliyuncs.com" in b:
        return b.replace("dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com")
    # If user passes some other host, don't change.
    return b


def _safe_json(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return resp.text


def _print_http_error(prefix: str, resp: requests.Response) -> None:
    data = _safe_json(resp)
    print(f"{prefix} HTTP {resp.status_code} body: {data}")


# ============================================================
# Base Wanx Client
# ============================================================

class _WanxBaseClient:
    def __init__(
        self,
        model: str,
        api_key: Optional[str] = None,
        base_url: str = "https://dashscope.aliyuncs.com",
        timeout: int = 120,
        max_retries: int = 1,
        poll_interval: float = 2.0,
    ):
        self.model = model
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.poll_interval = poll_interval

        if not self.api_key:
            raise ValueError("Wanx requires DASHSCOPE_API_KEY environment variable.")

    def _headers(self, async_enable: bool = False) -> Dict[str, str]:
        h = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if async_enable:
            # REQUIRED for these endpoints (async-only)
            h["X-DashScope-Async"] = "enable"
        return h

    # ------------------------------------------------------------
    # Task polling
    # ------------------------------------------------------------

    def _poll_task(self, task_id: str, base_url: Optional[str] = None) -> dict:
        """
        Poll async task until completion.
        """
        base = (base_url or self.base_url).rstrip("/")
        url = f"{base}/api/v1/tasks/{task_id}"

        start = time.time()
        while True:
            if time.time() - start > self.timeout:
                raise TimeoutError(f"Wanx task {task_id} timeout after {self.timeout}s.")

            resp = requests.get(url, headers=self._headers(async_enable=False), timeout=30)
            if resp.status_code >= 400:
                _print_http_error("[WANX-POLL]", resp)
                resp.raise_for_status()

            data = resp.json()
            status = data.get("output", {}).get("task_status")

            if status == "SUCCEEDED":
                return data
            if status == "FAILED":
                raise RuntimeError(f"Wanx task failed: {json.dumps(data, ensure_ascii=False)}")

            time.sleep(self.poll_interval)

    # ------------------------------------------------------------
    # Robust POST (with region fallback on 401/403)
    # ------------------------------------------------------------

    def _post_with_fallback(
        self,
        url_path: str,
        payload: Dict[str, Any],
        *,
        timeout: int = 60,
        async_enable: bool = True,
        tag: str = "[WANX]",
    ) -> Dict[str, Any]:
        """
        POST to base_url + url_path.
        If 401/403, auto retry with swapped region host once.
        """
        bases_to_try: List[str] = [self.base_url]
        alt = _swap_region_host(self.base_url)
        if alt != self.base_url:
            bases_to_try.append(alt)

        last_exc: Optional[Exception] = None

        for base in bases_to_try:
            url = f"{base.rstrip('/')}{url_path}"
            try:
                resp = requests.post(url, headers=self._headers(async_enable=async_enable), json=payload, timeout=timeout)
                if resp.status_code in (401, 403):
                    _print_http_error(f"{tag} AUTH", resp)
                    # try next base
                    continue
                if resp.status_code >= 400:
                    _print_http_error(f"{tag} ERR", resp)
                    resp.raise_for_status()
                return resp.json()
            except Exception as e:
                last_exc = e
                continue

        if last_exc:
            raise last_exc
        raise RuntimeError(f"{tag} request failed with unknown error.")


# ============================================================
# Text → Image
# ============================================================

class WanxImageGenClient(_WanxBaseClient):
    def generate(self, prompt: str, out_path: Path, size: str = "1024*1024") -> Path:
        """
        Generate image from text prompt (async task).
        Returns saved image path.
        """
        if out_path.exists():
            print(f"[WANX-GEN] Cache hit, reuse: {out_path}")
            return out_path

        print(f"[WANX-GEN] Requesting generation: model={self.model}")

        url_path = "/api/v1/services/aigc/text2image/image-synthesis"
        payload = {
            "model": self.model,
            "input": {"prompt": prompt},
            "parameters": {
                "n": 1,
                "size": size,
            },
        }

        # retry loop (cost-safe)
        for attempt in range(self.max_retries + 1):
            try:
                data = self._post_with_fallback(url_path, payload, timeout=60, async_enable=True, tag="[WANX-GEN]")
                task_id = data.get("output", {}).get("task_id")
                if not task_id:
                    raise RuntimeError(f"[WANX-GEN] Invalid response: {data}")

                print(f"[WANX-GEN] Task created: {task_id}")

                # IMPORTANT: poll must use SAME region base that created task.
                # The response may include request_id but not base; we infer by trying both in _poll_task via fallback is not safe.
                # Here we poll first with current base_url; if fails auth, user likely has region mismatch.
                final = self._poll_task(task_id, base_url=self.base_url)

                image_url = (final.get("output", {}).get("results", [{}])[0].get("url"))
                if not image_url:
                    raise RuntimeError(f"[WANX-GEN] No image URL in task result: {final}")

                img = requests.get(image_url, timeout=60)
                if img.status_code >= 400:
                    _print_http_error("[WANX-GEN-DL]", img)
                    img.raise_for_status()

                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(img.content)

                print(f"[WANX-GEN] Image saved to: {out_path}")
                return out_path

            except Exception as e:
                if attempt >= self.max_retries:
                    raise
                print(f"[WANX-GEN] retry {attempt+1}/{self.max_retries} after error: {e}")
                time.sleep(2)

        raise RuntimeError("[WANX-GEN] failed after retries.")


# ============================================================
# Image Edit (Image2Image)
# ============================================================

class WanxImageEditClient(_WanxBaseClient):
    def edit(self, image_path: Path, instruction: str, out_path: Path) -> Path:
        """
        Edit image using instruction.

        - qwen-image-edit*: try OpenAI-compatible Images API first, then fallback to native API
        - other models: keep legacy Wanx async Image2Image flow (URL input only)
        """
        # Determine log tag based on model
        log_tag = "QWEN-IMAGE-EDIT" if self.model.startswith("qwen-image-edit") else "WANX-EDIT"
        
        if out_path.exists():
            print(f"[{log_tag}] Cache hit, reuse: {out_path}")
            return out_path

        print(f"[{log_tag}] Preparing image for editing...")
        print(f"[{log_tag}] Input: {image_path}")

        if self.model.startswith("qwen-image-edit"):
            try:
                return self._edit_via_openai_compatible(image_path=image_path, instruction=instruction, out_path=out_path, log_tag=log_tag)
            except Exception as e:
                print(f"[{log_tag}] compatible Images API failed, fallback to native API: {type(e).__name__}: {e}")
                return self._edit_via_compatible_mode(image_path=image_path, instruction=instruction, out_path=out_path, log_tag=log_tag)

        return self._edit_via_legacy_wanx(image_path=image_path, instruction=instruction, out_path=out_path, log_tag=log_tag)

    def _edit_via_compatible_mode(self, image_path: Path, instruction: str, out_path: Path, log_tag: str = "QWEN-IMAGE-EDIT") -> Path:
        """Use DashScope native multimodal-generation API for qwen-image-edit series."""
        
        for attempt in range(self.max_retries + 1):
            try:
                # 1. 将本地图片转为 base64 data URL
                image_data_url = self._image_to_data_url(image_path)
                
                # 2. 构建 DashScope 原生接口请求
                url = f"{self.base_url.rstrip('/')}/api/v1/services/aigc/multimodal-generation/generation"
                
                payload = {
                    "model": self.model,
                    "input": {
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {"image": image_data_url},
                                    {"text": instruction}
                                ]
                            }
                        ]
                    }
                }
                
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }
                
                print(f"[{log_tag}] Requesting edit via native API...")
                resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
                
                if resp.status_code >= 400:
                    _print_http_error(f"[{log_tag}]", resp)
                    resp.raise_for_status()
                
                result = resp.json()
                
                # 3. 从响应中提取图片 URL
                # 响应格式: output.choices[0].message.content[*].image
                choices = result.get("output", {}).get("choices", [])
                if not choices:
                    raise RuntimeError(f"[{log_tag}] No choices in response: {result}")
                
                content = choices[0].get("message", {}).get("content", [])
                if not content:
                    raise RuntimeError(f"[{log_tag}] No content in message: {result}")
                
                # 查找第一个包含 image 字段的内容项
                image_url = None
                for item in content:
                    if isinstance(item, dict) and "image" in item:
                        image_url = item["image"]
                        break
                
                if not image_url:
                    raise RuntimeError(f"[{log_tag}] No image URL in response content: {content}")
                
                # 4. 下载图片并保存
                print(f"[{log_tag}] Downloading edited image from: {image_url}")
                img = requests.get(image_url, timeout=60)
                if img.status_code >= 400:
                    _print_http_error(f"[{log_tag}-DL]", img)
                    img.raise_for_status()
                
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(img.content)
                
                print(f"[{log_tag}] Edit completed: {out_path}")
                return out_path
                
            except Exception as e:
                if attempt >= self.max_retries:
                    raise
                print(f"[{log_tag}] Retry {attempt+1}/{self.max_retries} after error: {e}")
                time.sleep(2)

        raise RuntimeError(f"[{log_tag}] Failed after retries.")

    def _edit_via_openai_compatible(self, image_path: Path, instruction: str, out_path: Path, log_tag: str = "QWEN-IMAGE-EDIT") -> Path:
        """Use OpenAI-compatible /images/edits endpoint for qwen-image-edit series."""
        url = f"{self.base_url.rstrip('/')}/compatible-mode/v1/images/edits"

        for attempt in range(self.max_retries + 1):
            try:
                image_file = self._prepare_image_file(image_path)
                try:
                    files = {
                        "image": image_file,
                    }
                    data = {
                        "model": self.model,
                        "prompt": instruction,
                    }
                    headers = {
                        "Authorization": f"Bearer {self.api_key}",
                    }

                    print(f"[{log_tag}] Requesting edit via compatible Images API...")
                    resp = requests.post(url, headers=headers, data=data, files=files, timeout=self.timeout)
                finally:
                    if hasattr(image_file, "close"):
                        image_file.close()

                if resp.status_code >= 400:
                    _print_http_error(f"[{log_tag}]", resp)
                    resp.raise_for_status()

                result = resp.json()
                image_url = (result.get("data", [{}])[0].get("url"))
                if not image_url:
                    raise RuntimeError(f"[{log_tag}] No image URL in compatible response: {result}")

                print(f"[{log_tag}] Downloading edited image from: {image_url}")
                img = requests.get(image_url, timeout=60)
                if img.status_code >= 400:
                    _print_http_error(f"[{log_tag}-DL]", img)
                    img.raise_for_status()

                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(img.content)

                print(f"[{log_tag}] Edit completed: {out_path}")
                return out_path

            except Exception:
                if attempt >= self.max_retries:
                    raise
                time.sleep(2)

        raise RuntimeError(f"[{log_tag}] Failed after retries.")

    def _edit_via_legacy_wanx(self, image_path: Path, instruction: str, out_path: Path, log_tag: str = "WANX-EDIT") -> Path:
        image_str = str(image_path)
        if not (image_str.startswith("http://") or image_str.startswith("https://")):
            raise ValueError(
                "Legacy wanx image edit expects a public image URL. "
                "Please switch to qwen-image-edit series for local file editing."
            )

        url_path = "/api/v1/services/aigc/image2image/image-synthesis"
        payload = {
            "model": self.model,
            "input": {
                "sketch_image_url": image_str,
                "prompt": instruction,
            },
            "parameters": {
                "n": 1,
            },
        }

        for attempt in range(self.max_retries + 1):
            try:
                data = self._post_with_fallback(url_path, payload, timeout=60, async_enable=True, tag=f"[{log_tag}]")

                task_id = data.get("output", {}).get("task_id")
                if not task_id:
                    raise RuntimeError(f"[{log_tag}] Invalid response: {data}")

                final = self._poll_task(task_id, base_url=self.base_url)
                image_url_result = (final.get("output", {}).get("results", [{}])[0].get("url"))
                if not image_url_result:
                    raise RuntimeError(f"[{log_tag}] No image URL in result: {final}")

                img = requests.get(image_url_result, timeout=60)
                if img.status_code >= 400:
                    _print_http_error(f"[{log_tag}-DL]", img)
                    img.raise_for_status()

                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(img.content)

                print(f"[{log_tag}] Edit completed: {out_path}")
                return out_path
            except Exception as e:
                if attempt >= self.max_retries:
                    raise
                print(f"[{log_tag}] Retry {attempt+1}/{self.max_retries} after error: {e}")
                time.sleep(2)

        raise RuntimeError(f"[{log_tag}] Failed after retries.")

    def _image_to_data_url(self, image_path: Path) -> str:
        """
        Convert local image file to base64 data URL.
        Format: data:<mime>;base64,<base64_data>
        """
        path_str = str(image_path)
        
        # 如果是 URL，先下载
        if path_str.startswith("http://") or path_str.startswith("https://"):
            resp = requests.get(path_str, timeout=60)
            if resp.status_code >= 400:
                _print_http_error("[QWEN-IMAGE-EDIT-INPUT-DL]", resp)
                resp.raise_for_status()
            image_bytes = resp.content
            # 从 URL 推断 MIME 类型
            mime, _ = mimetypes.guess_type(path_str)
            if not mime or not mime.startswith("image/"):
                mime = "image/png"  # 默认
        else:
            # 本地文件
            if not image_path.exists():
                raise FileNotFoundError(f"Input image not found: {image_path}")
            
            mime, _ = mimetypes.guess_type(str(image_path))
            if not mime or not mime.startswith("image/"):
                # 尝试从扩展名推断
                ext = image_path.suffix.lower()
                mime_map = {
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".png": "image/png",
                    ".gif": "image/gif",
                    ".webp": "image/webp",
                }
                mime = mime_map.get(ext, "image/png")
            
            image_bytes = image_path.read_bytes()
        
        # 转为 base64
        b64_data = base64.b64encode(image_bytes).decode("utf-8")
        return f"data:{mime};base64,{b64_data}"
    
    def _prepare_image_file(self, image_path: Path):
        """Build a file-like object accepted by OpenAI images.edit."""
        path_str = str(image_path)
        if path_str.startswith("http://") or path_str.startswith("https://"):
            resp = requests.get(path_str, timeout=60)
            if resp.status_code >= 400:
                _print_http_error("[WANX-EDIT-INPUT-DL]", resp)
                resp.raise_for_status()
            suffix = Path(path_str).suffix or ".png"
            buf = BytesIO(resp.content)
            buf.name = f"input{suffix}"
            return buf

        if not image_path.exists():
            raise FileNotFoundError(f"Input image not found: {image_path}")

        mime, _ = mimetypes.guess_type(str(image_path))
        if mime and not mime.startswith("image/"):
            raise ValueError(f"Input file is not an image: {image_path}")
        return open(image_path, "rb")
