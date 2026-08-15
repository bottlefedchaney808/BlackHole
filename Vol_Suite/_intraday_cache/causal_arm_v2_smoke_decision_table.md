# Causal-Arm v2 — Single Decision Table (ROUND-10.2)

- effective unique-day n = **31** (target 29; reached=True)
- locked h (from-breach) = **1** 10-min bucket(s); locked h (daily) = **1** trading day; horizon_locked=True
- cutoff integrity OK = True
- solver = numpy.lstsq(SVD); rank=11/11; cond=1.11e+03; max_VIF=9.916829017528643 (flag=False)
- β (pre_vanna×ΔIV) = -4.477249370793003  se=4.074078622562191  status=IDENTIFIABLE
- honest β power (one-sided) = 0.005 (available=True, reach_80=False, n_for_80=None)
- family rule = False  (same-sign families (SPY -0.016, QQQ -0.004))
- BOTH-CLOCK-CONFIRMED = NOT-CONFIRMED  (blocked by: family rule)

| unique_day | families | habitat | surprise | pre_vanna | gamma | ΔIV | ΔS | mkt | A6 | spill | fwd_h | daily | breach | daily_neg | breach_pos |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026100 | SPY | NONE | DESCRIPTI | 1 | -0 | 0.0100 | 0.0014 | 0.0044 | 0.0414 | 0.0043 | -0.0253 | -0.0100 | 0.0150 | True | True |
| 2026101 | QQQ | NONE | DESCRIPTI | 1 | -0 | -0.0100 | -0.0011 | -0.0027 | -0.0391 | -0.0259 | -0.0282 | 0.0050 | -0.0080 | False | False |
| 2026102 | SPY | NONE | DESCRIPTI | 2 | 0 | 0.0100 | 0.0002 | 0.0033 | 0.0411 | -0.0126 | 0.0713 | 0.0050 | 0.0150 | False | True |
| 2026103 | QQQ | NONE | DESCRIPTI | 2 | 0 | -0.0100 | 0.0013 | -0.0019 | 0.0378 | -0.0045 | 0.1780 | -0.0100 | -0.0080 | True | False |
| 2026104 | SPY | NONE | DESCRIPTI | 1 | 0 | -0.0100 | -0.0005 | 0.0008 | -0.0254 | -0.0206 | 0.3029 | 0.0050 | 0.0150 | False | True |
| 2026105 | QQQ | NONE | DESCRIPTI | 2 | -0 | -0.0200 | -0.0016 | 0.0002 | 0.0090 | 0.0394 | -0.0800 | 0.0050 | -0.0080 | False | False |
| 2026106 | SPY | NONE | DESCRIPTI | 2 | 0 | 0.0100 | 0.0014 | -0.0019 | -0.0150 | -0.0415 | 0.4235 | -0.0100 | 0.0150 | True | True |
| 2026107 | QQQ | NONE | DESCRIPTI | 1 | -0 | -0.0100 | -0.0011 | -0.0012 | 0.0212 | 0.0224 | 0.0164 | 0.0050 | -0.0080 | False | False |
| 2026108 | SPY | NONE | DESCRIPTI | 1 | -0 | 0.0100 | 0.0009 | -0.0005 | 0.0224 | 0.0196 | 0.0291 | 0.0050 | 0.0150 | False | True |
| 2026109 | QQQ | NONE | DESCRIPTI | 1 | -0 | 0.0200 | 0.0013 | -0.0007 | 0.0542 | 0.0200 | 0.1619 | -0.0100 | -0.0080 | True | False |
| 2026110 | SPY | NONE | DESCRIPTI | 1 | -0 | -0.0100 | 0.0004 | -0.0022 | -0.0182 | -0.0103 | -0.0141 | 0.0050 | 0.0150 | False | True |
| 2026111 | QQQ | NONE | DESCRIPTI | 1 | -0 | -0.0200 | -0.0022 | -0.0024 | 0.0106 | 0.0302 | 0.0318 | 0.0050 | -0.0080 | False | False |
| 2026112 | SPY | NONE | DESCRIPTI | 2 | 0 | -0.0100 | -0.0022 | -0.0010 | 0.0007 | 0.0077 | -0.3435 | -0.0100 | 0.0150 | True | True |
| 2026113 | QQQ | NONE | DESCRIPTI | 2 | 0 | -0.0200 | 0.0027 | -0.0002 | -0.0010 | 0.0002 | -0.0036 | 0.0050 | -0.0080 | False | False |
| 2026114 | SPY | NONE | DESCRIPTI | 2 | -0 | 0.0200 | -0.0031 | -0.0024 | 0.0015 | 0.0456 | -0.1688 | 0.0050 | 0.0150 | False | True |
| 2026115 | QQQ | NONE | DESCRIPTI | 1 | -0 | 0.0200 | -0.0028 | 0.0005 | -0.0160 | 0.0084 | 0.2411 | -0.0100 | -0.0080 | True | False |
| 2026116 | SPY | NONE | DESCRIPTI | 1 | -0 | -0.0100 | -0.0019 | 0.0023 | -0.0011 | 0.0119 | -0.6503 | 0.0050 | 0.0150 | False | True |
| 2026117 | QQQ | NONE | DESCRIPTI | 2 | -0 | 0.0200 | 0.0023 | 0.0028 | -0.0098 | 0.0070 | 0.2368 | 0.0050 | -0.0080 | False | False |
| 2026118 | SPY | NONE | DESCRIPTI | 2 | -0 | -0.0100 | -0.0017 | 0.0033 | 0.0588 | 0.0102 | -0.0869 | -0.0100 | 0.0150 | True | True |
| 2026119 | QQQ | NONE | DESCRIPTI | 2 | -0 | -0.0100 | -0.0010 | -0.0023 | -0.0149 | -0.0138 | -0.1259 | 0.0050 | -0.0080 | False | False |
| 2026120 | SPY | NONE | DESCRIPTI | 1 | -0 | -0.0200 | -0.0020 | -0.0012 | -0.0191 | 0.0023 | 0.1147 | 0.0050 | 0.0150 | False | True |
| 2026121 | QQQ | NONE | DESCRIPTI | 2 | -0 | 0.0100 | 0.0010 | 0.0001 | 0.0535 | -0.0123 | -0.0750 | -0.0100 | -0.0080 | True | False |
| 2026122 | SPY | NONE | DESCRIPTI | 2 | -0 | -0.0200 | 0.0004 | 0.0026 | -0.0127 | -0.0248 | 0.0254 | 0.0050 | 0.0150 | False | True |
| 2026123 | QQQ | NONE | DESCRIPTI | 1 | -0 | 0.0100 | 0.0015 | 0.0012 | -0.0152 | 0.0158 | 0.1140 | 0.0050 | -0.0080 | False | False |
| 2026124 | SPY | NONE | DESCRIPTI | 2 | -0 | -0.0200 | -0.0017 | 0.0002 | -0.0090 | -0.0095 | -0.1591 | -0.0100 | 0.0150 | True | True |
| 2026125 | QQQ | NONE | DESCRIPTI | 1 | 0 | 0.0200 | 0.0005 | -0.0005 | -0.0176 | 0.0154 | 0.1543 | 0.0050 | -0.0080 | False | False |
| 2026126 | SPY | NONE | DESCRIPTI | 1 | -0 | -0.0200 | 0.0032 | 0.0014 | -0.0114 | 0.0215 | -0.3172 | 0.0050 | 0.0150 | False | True |
| 2026127 | QQQ | NONE | DESCRIPTI | 2 | -0 | -0.0200 | 0.0001 | 0.0020 | -0.0020 | 0.0052 | -0.0408 | -0.0100 | -0.0080 | True | False |
| 2026128 | SPY | NONE | DESCRIPTI | 2 | 0 | 0.0200 | 0.0014 | -0.0043 | 0.0194 | 0.0433 | 0.3007 | 0.0050 | 0.0150 | False | True |
| 2026129 | QQQ | NONE | DESCRIPTI | 1 | 0 | 0.0200 | -0.0010 | -0.0007 | -0.0079 | 0.0097 | 0.3053 | 0.0050 | -0.0080 | False | False |
| 2026130 | SPY | NONE | DESCRIPTI | 2 | -0 | -0.0100 | 0.0010 | 0.0020 | -0.0116 | 0.0001 | -0.0299 | -0.0100 | 0.0150 | True | True |
