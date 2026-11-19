# Ordering code parser

[简体中文](README.md) · English

Pass the complete scanner input to `parse_order_code.py` on standard input. The parser accepts spaces and line breaks and prints JSON to standard output.

```bash
printf '%s\n' 'ED-HMI3120-070C-10232' 'ES264500508' | python3 parse_order_code.py
```

```json
{"product_model":"ED-HMI3120","configuration":{"screen":"070","wifi":"1","ddr":"02","emmc":"32"},"sn":"ES264500508"}
```

An optional trailing serial number consists of `ES` followed by nine digits, separated from the ordering code by whitespace. The parser returns it as `sn` when present.

For device configuration, run `parse_order_code.py --strict` or `scan_and_parse.py --strict`. Strict mode requires a complete, unambiguous match and returns the matched string as `order_code`.
