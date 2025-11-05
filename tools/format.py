import re
import json
from typing import List

code_pattern = re.compile(r"```.*?\n(.*?)\n```", re.DOTALL)


def extract_first_number(text):
    match = re.search(r'\d+', text)
    return int(match.group()) if match else None


def extract_code(text):
    codes = code_pattern.findall(text)
    if codes:
        return codes[-1].strip()  # 返回最后一个匹配的代码块
    return ""


def json_to_markdown(content, language=""):
    if isinstance(content, str):
        content = json.loads(content)
    content = json.dumps(content, indent=2)
    return f"```{language}\n{content}\n```"


def parse_function_call(function_call):
    pattern = r"(?P<name>\w+)\((?P<params>.*)\)"
    match = re.match(pattern, function_call.strip())

    if not match:
        return None

    function_name = match.group("name")
    params_str = match.group("params")

    param_pattern = r"(?P<key>\w+)\s*=\s*(?P<value>[^,]+)"

    params = re.findall(param_pattern, params_str)

    params_dict = {key: eval(value) for key, value in params}
    result = {"name": function_name, "parameters": params_dict}

    return result


def construct_ordered_list(items: List[str]) -> str:
    return "\n".join([f"{i+1}. {item}" for i, item in enumerate(items)])


def test_extract_code():
    text = """
test

```python
def test():
    print("test")
```

thanks
"""
    print(extract_code(text))


def test_parse_function_call():
    function_call = 'call(max_tokens=2048, n=1, temperature=0.8, top_p=1, name="yes")'

    print(json.dumps(parse_function_call(function_call), indent=2))


def constrcut_openai_qa(query, response):
    return [
        {
            "role": "user",
            "content": query,
        },
        {
            "role": "assistant",
            "content": response,
        },
    ]


if __name__ == "__main__":
    test_extract_code()
    test_parse_function_call()
