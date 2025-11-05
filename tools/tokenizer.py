from transformers import AutoTokenizer

tokenizer = AutoTokenizer.from_pretrained("tools/tokenizer")


def get_token_num(text: str):
    return len(tokenizer.encode(text))
