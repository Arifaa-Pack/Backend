| Measure | 10 users | 30 users | 50 users | 50 users, worker ×10 | 10 users, real AI |
|---|---|---|---|---|---|
| Mix (web / WhatsApp / staff) | 5 / 4 / 1 | 15 / 12 / 3 | 25 / 20 / 5 | 25 / 20 / 5 | 5 / 4 / 1 |
| Duration, requests / errors | 8 min, 602 / 0 (0.0 %) | 8 min, 1881 / 0 (0.0 %) | 8 min, 3140 / 0 (0.0 %) | 8 min, 3115 / 8 (0.26 %) | 5 min, 420 / 83 (19.76 %) |
| Throughput | 1.25 req/s | 3.92 req/s | 6.54 req/s | 6.49 req/s | 1.4 req/s |
| CRM deal list p50 / p95 | 10 / 16 ms | 10 / 20 ms | 11 / 23 ms | 11 / 20 ms | 10 / 16 ms |
| CRM deal page p50 / p95 | 7 / 9 ms | 7 / 12 ms | 8 / 17 ms | 8 / 16 ms | 7 / 20 ms |
| CRM inbox p50 / p95 | 9 / 15 ms | 9 / 15 ms | 9 / 19 ms | 10 / 20 ms | 9 / 13 ms |
| WhatsApp webhook p50 / p95 | 14 / 47 ms | 14 / 42 ms | 14 / 105 ms | 15 / 43 ms | 13 / 42 ms |
| Website session p50 / p95 | 13 / 698 ms | 11 / 776 ms | 11 / 679 ms | 12 / 1149 ms | 13 / 170 ms |
| Website AI turn p50 / p95 | 5.8 / 11.6 s | 6.0 / 12.0 s | 6.1 / 12.1 s | 6.0 / 12.2 s | 2.2 / 7.5 s |
| WhatsApp messages answered | 132 / 132 | 415 / 415 | 677 / 677 | 678 / 678 | 50 / 50 |
| **WhatsApp reply delivered to buyer** p50 / p95 / max | 21.9 / 64.2 / 121.4 s | 89.7 / 224.0 / 259.8 s | 166.9 / 376.0 / 437.3 s | 33.5 / 159.1 / 188.0 s | 29.2 / 127.9 / 150.9 s |
| WhatsApp replies delivered within 60 s | 92.6 % | 39.8 % | 15.2 % | 68.4 % | 51.1 % |
| WhatsApp reply created (AI done) p50 / p95 / max | 12.6 / 33.62 / 49.0 s | 12.04 / 31.02 / 46.0 s | 19.54 / 48.66 / 66.14 s | 11.64 / 32.09 / 44.22 s | 28.6 / 98.07 / 138.07 s |
| Job backlog peak / oldest waiting | 49 / 76.4 s | 388 / 1333.0 s | 1446 / 335.4 s | 431 / 970.2 s | 29 / 32.1 s |
| Postgres connections peak | 35 | 34 | 34 | 80 | 37 |
| API: CPU avg / peak, RAM peak | 3.1 / 22.4 %, 324 MiB | 6.7 / 32.0 %, 320 MiB | 11.1 / 99.8 %, 326 MiB | 10.1 / 33.7 %, 327 MiB | 5.0 / 75.5 %, 328 MiB |
| Worker: CPU avg / peak, RAM peak | 5.5 / 28.3 %, 904 MiB | 5.9 / 80.6 %, 915 MiB | 7.1 / 117.7 %, 932 MiB | 17.3 / 137.4 %, 2591 MiB | 4.5 / 26.8 %, 924 MiB |
| Beat: CPU avg / peak, RAM peak | 1.2 / 22.5 %, 171 MiB | 1.1 / 22.0 %, 161 MiB | 1.6 / 22.3 %, 137 MiB | 1.2 / 24.8 %, 102 MiB | 1.1 / 24.0 %, 76 MiB |
| Postgres: CPU avg / peak, RAM peak | 2.1 / 8.7 %, 210 MiB | 2.8 / 20.5 %, 201 MiB | 3.9 / 34.9 %, 220 MiB | 5.7 / 32.0 %, 434 MiB | 1.9 / 15.6 %, 296 MiB |
| Redis: CPU avg / peak, RAM peak | 0.8 / 3.1 %, 8 MiB | 0.8 / 3.9 %, 7 MiB | 0.9 / 4.2 %, 8 MiB | 1.0 / 5.2 %, 7 MiB | 0.8 / 3.2 %, 5 MiB |
| Stalwart: CPU avg / peak, RAM peak | 0.1 / 3.4 %, 133 MiB | 0.1 / 2.8 %, 137 MiB | 0.1 / 3.1 %, 126 MiB | 0.1 / 3.1 %, 88 MiB | 0.1 / 3.0 %, 74 MiB |
| Phoenix: CPU avg / peak, RAM peak | 2.1 / 14.9 %, 254 MiB | 2.6 / 16.2 %, 253 MiB | 2.7 / 13.2 %, 257 MiB | 4.5 / 20.0 %, 258 MiB | 2.2 / 9.9 %, 256 MiB |
| **Platform total** (all kxlive containers): CPU avg, RAM peak | 17.6 % , 2.07 GiB | 22.8 % , 2.05 GiB | 31.1 % , 2.08 GiB | 44.3 % , 3.84 GiB | 17.9 % , 2.01 GiB |
