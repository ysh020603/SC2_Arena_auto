import time
import random
import os
import json
from openai import OpenAI

from tools.format import extract_code
from tools.common import pause_for_continue

class LLMClient:
    def __init__(self, base_url, api_key):
        self.client = OpenAI(
            base_url=base_url,
            api_key=api_key,
        )

    def call(
        self,
        model_name: str,
        prompt: str,
        history=[],
        n=1,
        max_tokens=2048,
        temperature=0.8,
        top_p=1,
        top_k=40,
        repetition_penalty=1.0,
        presence_penalty=0.0,
        timeout=180,
        system_message=None,
        retry_times=5,
        retry_interval=5,
        need_json=False,
    ):
        messages = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": prompt})

        def call_thread():
            completion = self.client.chat.completions.create(
                model=model_name,
                messages=messages,
                max_tokens=max_tokens,
                n=n,
                temperature=temperature,
                top_p=top_p,
                # top_k=top_k,
                # repetition_penalty=repetition_penalty,
                # presence_penalty=presence_penalty,
                timeout=timeout,
            )

            response = completion.choices[0].message.content.strip()
            return response

        for _ in range(retry_times):
            try:
                response = call_thread()
                if need_json:
                    resp_json = json.loads(extract_code(response))
                    assert isinstance(resp_json, dict) or isinstance(
                        resp_json, list
                    ), f"Response is not a valid JSON: {response}"
                messages.append({"role": "assistant", "content": response})
                return response, messages
            except Exception as e:
                print("Error while calling LLM service:", e)
                # import pdb; pdb.set_trace()
                time.sleep(retry_interval)
                continue

        response = "```\n[]\n```"
        messages.append({"role": "assistant", "content": response})
        return response, messages
