# Causal-Arm v2 — ≥29-Day Acquisition Result (ROUND-10.4 finalize)

**Date:** 2026-08-15  **Source:** `Vol_Suite/_causal_acquisition_20260815/records_merged/` (network-free finalize)

- Flattened day-records: **124** (ticker,day) across **62 unique calendar days** (≥29 target: REACHED)
- cutoff_pass=True: 124/124
- breach-eligible records: 48
- event-habitat records: 22

## Single Decision Table

# Causal-Arm v2 — Single Decision Table (ROUND-10.2)

- effective unique-day n = **62** (target 29; reached=True)
- locked h (from-breach) = **1** 10-min bucket(s); locked h (daily) = **1** trading day; horizon_locked=True
- cutoff integrity OK = True
- solver = numpy.lstsq(SVD); rank=11/11; cond=3.51e+12; max_VIF=85.12696144137864 (flag=True)
- β (pre_vanna×ΔIV) = 2.656513974449802e-09  se=2.2548908305852237e-09  status=IDENTIFIABLE
- honest β power (one-sided) = 0.311 (available=True, reach_80=False, n_for_80=278)
- family rule = False  (same-sign families (SPY -0.000, QQQ -0.000))
- BOTH-CLOCK-CONFIRMED = NOT-CONFIRMED  (blocked by: family rule)

| unique_day | families | habitat | surprise | pre_vanna | gamma | ΔIV | ΔS | mkt | A6 | spill | fwd_h | daily | breach | daily_neg | breach_pos |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 20260105 | QQQ,SPY | NONE | DESCRIPTI | 1272908 | 0 | 0.2662 | -0.0020 | 0.0013 | 0.7067 | 0.0066 | -0.0008 | -0.0010 | — | True | None |
| 20260106 | QQQ,SPY | NONE | DESCRIPTI | -466538 | 7507056 | 0.2654 | 0.0106 | 0.0100 | -0.3560 | 0.0011 | -0.0000 | 0.0053 | — | False | None |
| 20260107 | QQQ,SPY | NONE | DESCRIPTI | -89856 | 0 | 0.2747 | -0.0012 | -0.0067 | 1.0261 | 0.0111 | 0.0002 | -0.0006 | — | True | None |
| 20260108 | QQQ,SPY | NONE | DESCRIPTI | 151831 | 0 | 0.3398 | -0.0021 | 0.0020 | -0.4430 | 0.0082 | -0.0006 | -0.0010 | — | True | None |
| 20260109 | QQQ,SPY | NONE | DESCRIPTI | -261821 | 37320554 | 0.2310 | 0.0144 | 0.0093 | -0.4548 | 0.0101 | 0.0002 | 0.0072 | 0.0003 | False | True |
| 20260112 | QQQ,SPY | NONE | DESCRIPTI | 222028 | 9367569 | 0.2872 | 0.0116 | 0.0111 | 0.7199 | 0.0010 | 0.0019 | 0.0058 | — | False | None |
| 20260113 | QQQ,SPY | NONE | DESCRIPTI | -71901 | 1264270 | 0.2811 | -0.0056 | -0.0060 | 0.5882 | 0.0007 | -0.0007 | -0.0028 | 0.0001 | True | True |
| 20260114 | QQQ,SPY | NONE | DESCRIPTI | -384966 | 28147379 | 0.3032 | -0.0050 | -0.0023 | -0.8468 | 0.0055 | -0.0020 | -0.0025 | -0.0001 | True | False |
| 20260115 | QQQ,SPY | NONE | DESCRIPTI | -238570 | 0 | 0.3098 | -0.0098 | -0.0054 | 0.0147 | 0.0088 | 0.0007 | -0.0049 | — | True | None |
| 20260116 | QQQ,SPY | OPEX | DESCRIPTI | 333281 | 998860 | 0.2361 | -0.0109 | -0.0064 | -0.4176 | 0.0090 | -0.0006 | -0.0055 | — | True | None |
| 20260120 | QQQ,SPY | NONE | DESCRIPTI | 44908 | 12011908 | 0.4306 | -0.0131 | -0.0141 | 0.4686 | 0.0021 | -0.0012 | -0.0065 | 0.0001 | True | True |
| 20260121 | QQQ,SPY | EARNINGS | DESCRIPTI | 8214 | 185461042 | 0.3481 | 0.0176 | 0.0148 | -0.0204 | 0.0055 | -0.0019 | 0.0088 | -0.0010 | False | False |
| 20260122 | QQQ,SPY | EARNINGS | DESCRIPTI | -75173 | 0 | 0.2778 | -0.0020 | -0.0026 | -0.4992 | 0.0011 | 0.0002 | -0.0010 | — | True | None |
| 20260123 | QQQ,SPY | EARNINGS | DESCRIPTI | 48132 | 0 | 0.3008 | 0.0058 | 0.0027 | 0.5303 | 0.0062 | 0.0013 | 0.0029 | — | False | None |
| 20260126 | QQQ,SPY | NONE | DESCRIPTI | -399664 | 43449194 | 0.3254 | 0.0086 | 0.0073 | 0.4925 | 0.0026 | 0.0028 | 0.0043 | — | False | None |
| 20260127 | QQQ,SPY | FOMC | DESCRIPTI | 462477 | 0 | 0.3656 | 0.0054 | 0.0039 | 0.6901 | 0.0032 | 0.0002 | 0.0027 | — | False | None |
| 20260128 | QQQ,SPY | FOMC | DESCRIPTI | 83458 | 0 | 0.3922 | -0.0068 | -0.0059 | 0.3341 | 0.0018 | 0.0005 | -0.0034 | — | True | None |
| 20260129 | QQQ,SPY | EARNINGS | DESCRIPTI | -302207 | 632645328 | 0.3380 | -0.0092 | -0.0076 | -0.5886 | 0.0034 | -0.0013 | -0.0046 | -0.0007 | True | False |
| 20260130 | QQQ,SPY | NONE | DESCRIPTI | 245426 | 128325615 | 0.3506 | -0.0100 | -0.0028 | -0.2382 | 0.0144 | -0.0042 | -0.0050 | — | True | None |
| 20260202 | QQQ,SPY | NONE | DESCRIPTI | -41368 | 1250978 | 0.3374 | 0.0161 | 0.0143 | -0.2565 | 0.0037 | -0.0003 | 0.0081 | 0.0000 | False | True |
| 20260203 | QQQ,SPY | NONE | DESCRIPTI | -276265 | 240836838 | 0.4241 | -0.0296 | -0.0206 | -0.0524 | 0.0178 | -0.0007 | -0.0148 | -0.0004 | True | False |
| 20260204 | QQQ,SPY | NONE | DESCRIPTI | -157896 | 1910652498 | 0.5829 | -0.0179 | -0.0109 | 0.4428 | 0.0139 | -0.0003 | -0.0089 | -0.0001 | True | False |
| 20260205 | QQQ,SPY | NONE | DESCRIPTI | -2172742 | 167297112 | 0.6751 | -0.0067 | -0.0075 | -0.8494 | 0.0015 | -0.0024 | -0.0034 | — | True | None |
| 20260206 | QQQ,SPY | NONE | DESCRIPTI | -29022 | 414789574 | 0.3578 | 0.0300 | 0.0267 | -1.1948 | 0.0064 | -0.0009 | 0.0150 | -0.0005 | False | False |
| 20260209 | QQQ,SPY | NONE | DESCRIPTI | -121964 | 22736896 | 0.3620 | 0.0214 | 0.0150 | -0.3395 | 0.0129 | -0.0004 | 0.0107 | -0.0002 | False | False |
| 20260210 | QQQ,SPY | NONE | DESCRIPTI | -3751 | 0 | 0.4170 | -0.0107 | -0.0088 | -0.6406 | 0.0038 | -0.0001 | -0.0053 | — | True | None |
| 20260211 | QQQ,SPY | NONE | DESCRIPTI | -112087 | 110616330 | 0.3735 | -0.0143 | -0.0145 | 0.3539 | 0.0002 | 0.0004 | -0.0072 | 0.0002 | True | True |
| 20260212 | QQQ,SPY | NONE | DESCRIPTI | -84013 | 12914611121 | 0.5900 | -0.0417 | -0.0375 | -0.0312 | 0.0085 | -0.0005 | -0.0209 | -0.0002 | True | False |
| 20260213 | QQQ,SPY | NONE | DESCRIPTI | -260719 | 1390352 | 0.3968 | 0.0021 | -0.0004 | -0.4577 | 0.0050 | 0.0007 | 0.0011 | — | False | None |
| 20260217 | QQQ,SPY | NONE | DESCRIPTI | -138361 | 354585 | 0.4587 | 0.0092 | 0.0063 | -0.2909 | 0.0058 | 0.0038 | 0.0046 | 0.0007 | False | True |
| 20260218 | QQQ,SPY | NONE | DESCRIPTI | -194389 | 132017350 | 0.4109 | 0.0118 | 0.0090 | 0.3774 | 0.0056 | 0.0005 | 0.0059 | 0.0003 | False | True |
| 20260219 | QQQ,SPY | NONE | DESCRIPTI | 566334 | 0 | 0.4036 | 0.0007 | 0.0009 | -0.2914 | 0.0006 | 0.0000 | 0.0003 | — | False | None |
| 20260220 | QQQ,SPY | OPEX | DESCRIPTI | -157615 | 2055999 | 0.3546 | 0.0247 | 0.0216 | -0.0677 | 0.0062 | -0.0031 | 0.0124 | -0.0016 | False | False |
| 20260223 | QQQ,SPY | NONE | DESCRIPTI | -58000 | 978771802 | 0.5067 | -0.0195 | -0.0195 | -0.5467 | 0.0000 | -0.0012 | -0.0097 | -0.0006 | True | False |
| 20260224 | QQQ,SPY | NONE | DESCRIPTI | -111356 | 6423567 | 0.4318 | 0.0169 | 0.0165 | 0.7747 | 0.0008 | -0.0002 | 0.0085 | -0.0001 | False | False |
| 20260225 | QQQ,SPY | NONE | DESCRIPTI | 17882 | 0 | 0.4078 | 0.0099 | 0.0061 | -0.4122 | 0.0076 | 0.0002 | 0.0050 | — | False | None |
| 20260226 | QQQ,SPY | NONE | DESCRIPTI | -99419 | 689295786 | 0.3640 | -0.0127 | -0.0086 | 0.2528 | 0.0080 | -0.0015 | -0.0063 | -0.0008 | True | False |
| 20260227 | QQQ,SPY | NONE | DESCRIPTI | 37424 | 0 | 0.3894 | 0.0111 | 0.0083 | -0.4744 | 0.0055 | -0.0006 | 0.0055 | — | False | None |
| 20260302 | QQQ,SPY | NONE | DESCRIPTI | -45202 | 216413539 | 0.4449 | 0.0243 | 0.0211 | 0.1322 | 0.0064 | -0.0005 | 0.0121 | -0.0003 | False | False |
| 20260303 | QQQ,SPY | NONE | DESCRIPTI | 66764 | 500138 | 0.5215 | 0.0119 | 0.0120 | -0.5673 | 0.0002 | 0.0005 | 0.0060 | 0.0007 | False | True |
| 20260304 | QQQ,SPY | NONE | DESCRIPTI | -3868 | 24720268 | 0.4469 | 0.0109 | 0.0070 | -0.4552 | 0.0079 | -0.0008 | 0.0055 | 0.0000 | False | True |
| 20260305 | QQQ,SPY | NONE | DESCRIPTI | 193615 | 101373296 | 0.4839 | -0.0011 | -0.0031 | 0.3681 | 0.0041 | 0.0010 | -0.0006 | 0.0007 | True | True |
| 20260306 | QQQ,SPY | NONE | DESCRIPTI | -12039 | 0 | 0.6197 | -0.0013 | -0.0011 | 0.4698 | 0.0004 | -0.0009 | -0.0006 | — | True | None |
| 20260309 | QQQ,SPY | NONE | DESCRIPTI | -71750 | 15720269 | 0.5130 | 0.0358 | 0.0325 | 0.0603 | 0.0064 | 0.0022 | 0.0179 | 0.0011 | False | True |
| 20260310 | QQQ,SPY | NONE | DESCRIPTI | 10428 | 12936366 | 0.5259 | -0.0045 | -0.0041 | -0.3491 | 0.0008 | 0.0005 | -0.0022 | -0.0001 | True | False |
| 20260311 | QQQ,SPY | NONE | DESCRIPTI | -320110 | 32409779 | 0.4581 | -0.0066 | -0.0053 | 0.1197 | 0.0027 | -0.0008 | -0.0033 | -0.0000 | True | False |
| 20260312 | QQQ,SPY | NONE | DESCRIPTI | -588548 | 75968597 | 0.6017 | -0.0160 | -0.0137 | -0.6292 | 0.0047 | -0.0024 | -0.0080 | — | True | None |
| 20260313 | QQQ,SPY | NONE | DESCRIPTI | -55889 | 6693775 | 0.5349 | -0.0226 | -0.0232 | 0.2763 | 0.0012 | 0.0001 | -0.0113 | 0.0001 | True | True |
| 20260316 | QQQ,SPY | NONE | DESCRIPTI | -9915241 | 0 | 0.4718 | 0.0019 | 0.0022 | -0.0600 | 0.0006 | 0.0014 | 0.0009 | — | False | None |
| 20260317 | QQQ,SPY | FOMC | DESCRIPTI | -2883 | 0 | 0.4043 | -0.0025 | -0.0057 | 0.6501 | 0.0065 | 0.0009 | -0.0013 | — | True | None |
| 20260318 | QQQ,SPY | FOMC | DESCRIPTI | -44439 | 10588539 | 0.4902 | -0.0206 | -0.0193 | -0.1504 | 0.0027 | 0.0009 | -0.0103 | — | True | None |
| 20260319 | QQQ,SPY | NONE | DESCRIPTI | 78313 | 283865876 | 0.4112 | 0.0142 | 0.0119 | 0.8131 | 0.0046 | -0.0005 | 0.0071 | -0.0003 | False | False |
| 20260320 | QQQ,SPY | OPEX | DESCRIPTI | 44491 | 1067145025 | 0.5167 | -0.0238 | -0.0221 | 0.6577 | 0.0033 | -0.0012 | -0.0119 | -0.0006 | True | False |
| 20260323 | QQQ,SPY | NONE | DESCRIPTI | -786948 | 2251383 | 0.5528 | -0.0062 | -0.0065 | 0.3778 | 0.0005 | 0.0012 | -0.0031 | — | True | None |
| 20260324 | QQQ,SPY | NONE | DESCRIPTI | -16033 | 1004019 | 0.5528 | 0.0007 | 0.0052 | -0.5759 | 0.0089 | 0.0031 | 0.0003 | — | False | None |
| 20260325 | QQQ,SPY | NONE | DESCRIPTI | 104385 | 1461666973 | 0.4932 | -0.0078 | -0.0089 | -0.0978 | 0.0022 | 0.0007 | -0.0039 | — | True | None |
| 20260326 | QQQ,SPY | NONE | DESCRIPTI | -127964 | 147228698 | 0.5558 | -0.0233 | -0.0200 | 0.3190 | 0.0066 | 0.0001 | -0.0116 | 0.0000 | True | False |
| 20260406 | QQQ,SPY | NONE | DESCRIPTI | 77225 | 0 | 0.4712 | 0.0046 | 0.0065 | 0.3945 | 0.0038 | 0.0006 | 0.0023 | — | False | None |
| 20260407 | QQQ,SPY | NONE | DESCRIPTI | -185716 | 11014532 | 0.6259 | 0.0088 | 0.0080 | 0.0135 | 0.0018 | -0.0017 | 0.0044 | -0.0001 | False | False |
| 20260408 | QQQ,SPY | NONE | DESCRIPTI | -1150 | 592169 | 0.4111 | -0.0045 | -0.0012 | -0.3444 | 0.0067 | -0.0029 | -0.0023 | — | True | None |
| 20260409 | QQQ,SPY | NONE | DESCRIPTI | 6916993 | 41371364822 | 0.3342 | 0.0148 | 0.0156 | 0.6207 | 0.0016 | 0.0010 | 0.0074 | — | False | None |
| 20260410 | QQQ,SPY | NONE | DESCRIPTI | 18245 | 0 | 0.3167 | -0.0051 | -0.0065 | 0.3390 | 0.0029 | 0.0011 | -0.0025 | — | True | None |

## Honest power caveat (per R10.3/R10.4 disclosure)

- effective unique-day n = 62. The **≥29-day target sizes the correlational MDE arm (md≤0.5), NOT the causal L2 interaction β.**
- β-specific one-sided power = 0.311 (reach_80=False, n_for_80=278). This is the honest causal-arm power; it is NOT an 80% claim unless `beta_reach_80` is True.
- A positive/identifiable β is re-admission-to-evidence evidence only (after reverse/placebo/A6/gamma/market/spillover/opposite/family/both-clock rules + fresh R1→R2→R3 Cem). It NEVER auto-promotes. The model stays descriptive/conditional throughout.
