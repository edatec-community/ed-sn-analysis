# 订购编码解析接口

简体中文 · [English](README.en.md)

上层工具负责扫码，并把完整扫描文本传给 `parse_order_code.py`。通过标准输入传递时，空格、换行和较长内容都作为同一段输入处理；解析结果从标准输出返回 JSON。

```python
import json
import subprocess
import sys

scan_value = "ED-HMI3120-070C-10232\nES264500508"
result = subprocess.run(
    [sys.executable, "parse_order_code.py"],
    input=scan_value,
    text=True,
    capture_output=True,
    check=True,
)
parsed = json.loads(result.stdout)
```

返回示例：

```json
{"product_model":"ED-HMI3120","configuration":{"screen":"070","wifi":"1","ddr":"02","emmc":"32"},"sn":"ES264500508"}
```

普通模式和 `--strict` 模式均支持以空格或换行分隔、附在末尾的序列号。
序列号由 `ES` 和紧跟的 9 位数字组成，解析成功时返回 `sn` 字段。只输入订购编码时省略该字段。

用于设备配置时，可传入 `--strict`。该模式要求完整匹配一个订购编码，
并在成功结果中增加 `order_code`（完整匹配的订购编码）。匹配结果有歧义时返回错误。
扫码入口也接受 `--strict`。
