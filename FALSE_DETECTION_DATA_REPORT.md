# Droid Alerts Detection Classification Report

**Dataset:** `/Users/rubenvancraenenbroeck/Downloads/data 2`  
**Scope:** Beskar, Galactic, and Rainbow chat detections  
**Canonical ground truth:** `tests/data_report_manifest.json`

## Classification rule

- **Real:** The captured image visibly contains the exact droid family and rarity encoded by the detection folder.
- **False:** That exact family/rarity is absent, even if another valid alert is visible.
- **Uncertain:** The image is too obscured to label reliably and is excluded from scoring.

## Review correction

The original report treated recorded alert metadata as truth. Visual review found metadata-label contradictions, so the reviewed manifest is now authoritative. It contains 52 corrected labels: 5 false-to-real and 47 real-to-false.

## Overall summary

| Family | Reviewed | Real | False | Uncertain |
|---|---:|---:|---:|---:|
| Beskar | 645 | 611 | 32 | 2 |
| Galactic | 807 | 731 | 76 | 0 |
| Rainbow | 171 | 164 | 7 | 0 |
| **Total** | **1623** | **1506** | **115** | **2** |

## Per-category results

### Beskar

| Detection | Reviewed | Real | False | Uncertain |
|---|---:|---:|---:|---:|
| Beskar Epic | 337 | 318 | 18 | 1 |
| Beskar Legendary | 207 | 197 | 9 | 1 |
| Beskar Mythic | 101 | 96 | 5 | 0 |

### Galactic

| Detection | Reviewed | Real | False | Uncertain |
|---|---:|---:|---:|---:|
| Galactic Common | 302 | 287 | 15 | 0 |
| Galactic Rare | 207 | 199 | 8 | 0 |
| Galactic Epic | 154 | 146 | 8 | 0 |
| Galactic Legendary | 96 | 58 | 38 | 0 |
| Galactic Mythic | 48 | 41 | 7 | 0 |

### Rainbow

| Detection | Reviewed | Real | False | Uncertain |
|---|---:|---:|---:|---:|
| Rainbow Epic | 69 | 69 | 0 | 0 |
| Rainbow Legendary | 35 | 35 | 0 | 0 |
| Rainbow Mythic | 67 | 60 | 7 | 0 |

## False-detection locations

Each row is a reviewed negative for the exact recorded target. Paths are relative to the dataset root above.

### Beskar false detections (32)

| # | Recorded target | Submission | Resolution | Folder |
|---:|---|---|---:|---|
| 1 | Beskar Epic | `579d4e4f-ef0d-464f-8011-107ffce92913` | 1920x1080 | `2769a23a-9a83-4d4d-802f-2be74b0c8510/579d4e4f-ef0d-464f-8011-107ffce92913_beskarepic` |
| 2 | Beskar Epic | `c63a48f0-6dba-44ff-8696-7ce8f1b7c953` | 1920x1080 | `2769a23a-9a83-4d4d-802f-2be74b0c8510/c63a48f0-6dba-44ff-8696-7ce8f1b7c953_beskarepic` |
| 3 | Beskar Epic | `dd49a219-e5e3-40ce-bedf-0f00850602b1` | 2560x1080 | `6b9648fe-5ecc-46ac-b33b-854556290b46/dd49a219-e5e3-40ce-bedf-0f00850602b1_beskarepic` |
| 4 | Beskar Epic | `1d646165-d4dc-4fd7-8c23-28b51ca85495` | 1920x1080 | `889fbc8f-528d-43ef-9249-55d9926b206c/1d646165-d4dc-4fd7-8c23-28b51ca85495_beskarepic` |
| 5 | Beskar Epic | `46cf48b5-5bb9-43c4-a61c-312cb9065426` | 1920x1080 | `889fbc8f-528d-43ef-9249-55d9926b206c/46cf48b5-5bb9-43c4-a61c-312cb9065426_beskarepic` |
| 6 | Beskar Epic | `959c7d19-e0e3-4155-9d05-37dc83e3a01c` | 1920x1080 | `889fbc8f-528d-43ef-9249-55d9926b206c/959c7d19-e0e3-4155-9d05-37dc83e3a01c_beskarepic` |
| 7 | Beskar Epic | `c490bf49-aba1-45ee-b063-51666c8f3176` | 1920x1080 | `889fbc8f-528d-43ef-9249-55d9926b206c/c490bf49-aba1-45ee-b063-51666c8f3176_beskarepic` |
| 8 | Beskar Epic | `3c803046-d9ae-4338-aeda-9084822db662` | 2560x1440 | `8cc8e92c-d4f8-46a8-8745-f0f24249d504/3c803046-d9ae-4338-aeda-9084822db662_beskarepic` |
| 9 | Beskar Epic | `467a79d9-0287-4b27-8275-a45b96d0b1dc` | 2560x1440 | `8cc8e92c-d4f8-46a8-8745-f0f24249d504/467a79d9-0287-4b27-8275-a45b96d0b1dc_beskarepic` |
| 10 | Beskar Epic | `be167e75-b2eb-403f-bbdf-213c50ac9a74` | 2560x1440 | `8cc8e92c-d4f8-46a8-8745-f0f24249d504/be167e75-b2eb-403f-bbdf-213c50ac9a74_beskarepic` |
| 11 | Beskar Epic | `a2999748-46a2-42f8-9566-db8e6f7c8333` | 1920x1080 | `97ca164f-a796-41af-bfc4-d5df2b1a7b95/a2999748-46a2-42f8-9566-db8e6f7c8333_beskarepic` |
| 12 | Beskar Epic | `a3332e86-6a87-4e4e-931d-66841c27bf7f` | 1920x1080 | `97ca164f-a796-41af-bfc4-d5df2b1a7b95/a3332e86-6a87-4e4e-931d-66841c27bf7f_beskarepic` |
| 13 | Beskar Epic | `5d080546-ce71-4dc9-be3a-ab4b89aedec3` | 2560x1440 | `d1f4cb88-6a69-471a-8458-b251d8a268ea/5d080546-ce71-4dc9-be3a-ab4b89aedec3_beskarepic` |
| 14 | Beskar Epic | `699312f1-2602-4088-9259-e640276ffe61` | 2560x1440 | `d1f4cb88-6a69-471a-8458-b251d8a268ea/699312f1-2602-4088-9259-e640276ffe61_beskarepic` |
| 15 | Beskar Epic | `7c57e07c-3c04-42c6-89f2-bf48194b99f3` | 2560x1440 | `d1f4cb88-6a69-471a-8458-b251d8a268ea/7c57e07c-3c04-42c6-89f2-bf48194b99f3_beskarepic` |
| 16 | Beskar Epic | `c32f07aa-a3a0-4ddc-b618-f5f155db81f2` | 2560x1440 | `d1f4cb88-6a69-471a-8458-b251d8a268ea/c32f07aa-a3a0-4ddc-b618-f5f155db81f2_beskarepic` |
| 17 | Beskar Epic | `c6014075-f6ee-41af-b5f7-aff8be4497cd` | 2560x1440 | `d1f4cb88-6a69-471a-8458-b251d8a268ea/c6014075-f6ee-41af-b5f7-aff8be4497cd_beskarepic` |
| 18 | Beskar Epic | `e9230e3d-d4a6-4ec8-a938-56e3e2c70366` | 2560x1440 | `d1f4cb88-6a69-471a-8458-b251d8a268ea/e9230e3d-d4a6-4ec8-a938-56e3e2c70366_beskarepic` |
| 19 | Beskar Legendary | `5512462f-c478-46b2-bcdb-9153b22fd7eb` | 1920x1080 | `2769a23a-9a83-4d4d-802f-2be74b0c8510/5512462f-c478-46b2-bcdb-9153b22fd7eb_beskarlegendary` |
| 20 | Beskar Legendary | `f36f29c3-dac4-4686-84b1-f9164d9bf858` | 1920x1080 | `2769a23a-9a83-4d4d-802f-2be74b0c8510/f36f29c3-dac4-4686-84b1-f9164d9bf858_beskarlegendary` |
| 21 | Beskar Legendary | `6b8b1ca6-e0f0-4cf7-856b-543d71ff2133` | 2560x1440 | `8cc8e92c-d4f8-46a8-8745-f0f24249d504/6b8b1ca6-e0f0-4cf7-856b-543d71ff2133_beskarlegendary` |
| 22 | Beskar Legendary | `03c675ed-0a73-4ef0-9540-77fdf7943b73` | 1920x1080 | `97ca164f-a796-41af-bfc4-d5df2b1a7b95/03c675ed-0a73-4ef0-9540-77fdf7943b73_beskarlegendary` |
| 23 | Beskar Legendary | `20afeef5-9565-46df-8269-084061a9bc6b` | 1920x1080 | `97ca164f-a796-41af-bfc4-d5df2b1a7b95/20afeef5-9565-46df-8269-084061a9bc6b_beskarlegendary` |
| 24 | Beskar Legendary | `7fbed086-b881-42f3-964d-4c48b92060b4` | 1920x1080 | `97ca164f-a796-41af-bfc4-d5df2b1a7b95/7fbed086-b881-42f3-964d-4c48b92060b4_beskarlegendary` |
| 25 | Beskar Legendary | `93c8f65b-76f2-4395-8de2-74fcedf9b9a0` | 1920x1080 | `97ca164f-a796-41af-bfc4-d5df2b1a7b95/93c8f65b-76f2-4395-8de2-74fcedf9b9a0_beskarlegendary` |
| 26 | Beskar Legendary | `23e02d18-5fd8-4cb8-b583-a615a001690a` | 2560x1440 | `d1f4cb88-6a69-471a-8458-b251d8a268ea/23e02d18-5fd8-4cb8-b583-a615a001690a_beskarlegendary` |
| 27 | Beskar Legendary | `c96b7949-6b16-46f2-b770-7f71ecb47e5c` | 1920x1080 | `f779e268-a25c-4bbe-aa9e-1e87e83af247/c96b7949-6b16-46f2-b770-7f71ecb47e5c_beskarlegendary` |
| 28 | Beskar Mythic | `4ed52b04-b38a-4e39-a121-c3e4e26d82e1` | 2560x1440 | `25580b4b-c066-4db6-ab22-86b47389aef0/4ed52b04-b38a-4e39-a121-c3e4e26d82e1_beskarmythic` |
| 29 | Beskar Mythic | `a9396f31-0585-4934-91de-c6efd9dc9da0` | 1920x1080 | `2769a23a-9a83-4d4d-802f-2be74b0c8510/a9396f31-0585-4934-91de-c6efd9dc9da0_beskarmythic` |
| 30 | Beskar Mythic | `b97dd493-6e0c-4832-9f03-c416b373ab92` | 1920x1080 | `889fbc8f-528d-43ef-9249-55d9926b206c/b97dd493-6e0c-4832-9f03-c416b373ab92_beskarmythic` |
| 31 | Beskar Mythic | `9fa1376e-7b64-45ba-ade4-a88d1df0dcff` | 1920x1080 | `97ca164f-a796-41af-bfc4-d5df2b1a7b95/9fa1376e-7b64-45ba-ade4-a88d1df0dcff_beskarmythic` |
| 32 | Beskar Mythic | `1c4ce7ed-582d-48f1-9e77-ef95b6e911c2` | 1920x1080 | `f779e268-a25c-4bbe-aa9e-1e87e83af247/1c4ce7ed-582d-48f1-9e77-ef95b6e911c2_beskarmythic` |

### Galactic false detections (76)

| # | Recorded target | Submission | Resolution | Folder |
|---:|---|---|---:|---|
| 1 | Galactic Common | `074aca9b-37da-4b7e-b228-40500dda648f` | 2560x1080 | `6b9648fe-5ecc-46ac-b33b-854556290b46/074aca9b-37da-4b7e-b228-40500dda648f_galacticcommon` |
| 2 | Galactic Common | `2928047e-26fd-4094-8025-985fe40b851e` | 2560x1080 | `6b9648fe-5ecc-46ac-b33b-854556290b46/2928047e-26fd-4094-8025-985fe40b851e_galacticcommon` |
| 3 | Galactic Common | `8c0615a8-2b7d-42e1-8756-21daa3089637` | 2560x1080 | `6b9648fe-5ecc-46ac-b33b-854556290b46/8c0615a8-2b7d-42e1-8756-21daa3089637_galacticcommon` |
| 4 | Galactic Common | `a8871940-9dcb-4563-a9e1-ba2785023774` | 2560x1080 | `6b9648fe-5ecc-46ac-b33b-854556290b46/a8871940-9dcb-4563-a9e1-ba2785023774_galacticcommon` |
| 5 | Galactic Common | `b50dcaf0-6907-4b6b-bdcf-ae708be13ac5` | 2560x1080 | `6b9648fe-5ecc-46ac-b33b-854556290b46/b50dcaf0-6907-4b6b-bdcf-ae708be13ac5_galacticcommon` |
| 6 | Galactic Common | `e8bcea0a-b73c-4134-85a4-0d5e0b322f34` | 2560x1080 | `6b9648fe-5ecc-46ac-b33b-854556290b46/e8bcea0a-b73c-4134-85a4-0d5e0b322f34_galacticcommon` |
| 7 | Galactic Common | `fa239bba-53e6-4011-9983-48800317e2c7` | 2560x1080 | `6b9648fe-5ecc-46ac-b33b-854556290b46/fa239bba-53e6-4011-9983-48800317e2c7_galacticcommon` |
| 8 | Galactic Common | `0fd07ea1-e7a4-43ed-867e-03bd82c03817` | 1920x1080 | `b861c858-7bc9-46a0-a602-7a78f9321eda/0fd07ea1-e7a4-43ed-867e-03bd82c03817_galacticcommon` |
| 9 | Galactic Common | `6600bf08-c212-499d-8fe1-1043a09914cd` | 1920x1080 | `b861c858-7bc9-46a0-a602-7a78f9321eda/6600bf08-c212-499d-8fe1-1043a09914cd_galacticcommon` |
| 10 | Galactic Common | `84ab0da7-e6fe-4d1f-b91c-53032e9945ee` | 1920x1080 | `b861c858-7bc9-46a0-a602-7a78f9321eda/84ab0da7-e6fe-4d1f-b91c-53032e9945ee_galacticcommon` |
| 11 | Galactic Common | `998e1f44-4c27-4340-9185-f817be0b5c00` | 1920x1080 | `b861c858-7bc9-46a0-a602-7a78f9321eda/998e1f44-4c27-4340-9185-f817be0b5c00_galacticcommon` |
| 12 | Galactic Common | `2bb74f47-030a-4f68-8d63-0189db38d6ac` | 2560x1440 | `f6b1cf32-4b54-4245-8aad-2ac4c158f94f/2bb74f47-030a-4f68-8d63-0189db38d6ac_galacticcommon` |
| 13 | Galactic Common | `a6e64250-f6f6-44b1-aa19-e3f303324ac5` | 2560x1440 | `f6b1cf32-4b54-4245-8aad-2ac4c158f94f/a6e64250-f6f6-44b1-aa19-e3f303324ac5_galacticcommon` |
| 14 | Galactic Common | `dd7950b8-cbb3-420a-b954-e5017218c75e` | 2560x1440 | `f6b1cf32-4b54-4245-8aad-2ac4c158f94f/dd7950b8-cbb3-420a-b954-e5017218c75e_galacticcommon` |
| 15 | Galactic Common | `de1f0757-3059-4420-9ab2-b37faab77327` | 2560x1440 | `f6b1cf32-4b54-4245-8aad-2ac4c158f94f/de1f0757-3059-4420-9ab2-b37faab77327_galacticcommon` |
| 16 | Galactic Epic | `0a6dfabc-94c6-46b2-abd2-46289122bf61` | 2560x1080 | `6b9648fe-5ecc-46ac-b33b-854556290b46/0a6dfabc-94c6-46b2-abd2-46289122bf61_galacticepic` |
| 17 | Galactic Epic | `18e7b1de-6874-4419-ae7f-7062de3f6b39` | 2560x1080 | `6b9648fe-5ecc-46ac-b33b-854556290b46/18e7b1de-6874-4419-ae7f-7062de3f6b39_galacticepic` |
| 18 | Galactic Epic | `1a730377-a8ac-43fd-9101-f3b43e75795b` | 2560x1080 | `6b9648fe-5ecc-46ac-b33b-854556290b46/1a730377-a8ac-43fd-9101-f3b43e75795b_galacticepic` |
| 19 | Galactic Epic | `459b1393-92d1-4acf-8894-2c2fc6f625c4` | 2560x1080 | `6b9648fe-5ecc-46ac-b33b-854556290b46/459b1393-92d1-4acf-8894-2c2fc6f625c4_galacticepic` |
| 20 | Galactic Epic | `4e2b66ec-b524-436e-9222-1bd193b0f309` | 2560x1080 | `6b9648fe-5ecc-46ac-b33b-854556290b46/4e2b66ec-b524-436e-9222-1bd193b0f309_galacticepic` |
| 21 | Galactic Epic | `e1cfd352-2f46-48af-a906-52d1c16d4e97` | 2560x1080 | `6b9648fe-5ecc-46ac-b33b-854556290b46/e1cfd352-2f46-48af-a906-52d1c16d4e97_galacticepic` |
| 22 | Galactic Epic | `8a3868f7-e2d7-4c5d-8aae-77a0b87efdd2` | 2560x1440 | `9759dc8a-1229-4fc5-a5f3-6b8fd37ad570/8a3868f7-e2d7-4c5d-8aae-77a0b87efdd2_galacticepic` |
| 23 | Galactic Epic | `064d3a99-380a-4c7b-8a98-f6927fdccadf` | 2560x1440 | `f6b1cf32-4b54-4245-8aad-2ac4c158f94f/064d3a99-380a-4c7b-8a98-f6927fdccadf_galacticepic` |
| 24 | Galactic Legendary | `d45ca663-d80e-4db3-964b-b2c7de3027e3` | 1920x1080 | `2769a23a-9a83-4d4d-802f-2be74b0c8510/d45ca663-d80e-4db3-964b-b2c7de3027e3_galacticlegendary` |
| 25 | Galactic Legendary | `5ab0aef4-5197-444e-9025-43b85774ce8d` | 2560x1440 | `4e84d724-c207-4fb3-8da9-06b8ba237e87/5ab0aef4-5197-444e-9025-43b85774ce8d_galacticlegendary` |
| 26 | Galactic Legendary | `3610d6f7-b85a-4e33-ac0a-49cd72a46fac` | 2560x1080 | `6b9648fe-5ecc-46ac-b33b-854556290b46/3610d6f7-b85a-4e33-ac0a-49cd72a46fac_galacticlegendary` |
| 27 | Galactic Legendary | `98465a4c-125d-4bfc-b6ed-cb186802b03c` | 2560x1080 | `6b9648fe-5ecc-46ac-b33b-854556290b46/98465a4c-125d-4bfc-b6ed-cb186802b03c_galacticlegendary` |
| 28 | Galactic Legendary | `9a21641a-6831-4fbf-9148-3eff1ac1e061` | 2560x1080 | `6b9648fe-5ecc-46ac-b33b-854556290b46/9a21641a-6831-4fbf-9148-3eff1ac1e061_galacticlegendary` |
| 29 | Galactic Legendary | `e0a45dc4-9bec-40b2-951b-3ecf9a7580f9` | 2560x1080 | `6b9648fe-5ecc-46ac-b33b-854556290b46/e0a45dc4-9bec-40b2-951b-3ecf9a7580f9_galacticlegendary` |
| 30 | Galactic Legendary | `84ae2711-d56d-40cf-8c31-9fd51b7fc254` | 1920x1080 | `889fbc8f-528d-43ef-9249-55d9926b206c/84ae2711-d56d-40cf-8c31-9fd51b7fc254_galacticlegendary` |
| 31 | Galactic Legendary | `ca00e765-144c-4650-9e5f-e1a82d764584` | 2560x1440 | `8cc8e92c-d4f8-46a8-8745-f0f24249d504/ca00e765-144c-4650-9e5f-e1a82d764584_galacticlegendary` |
| 32 | Galactic Legendary | `13f3a0f8-7ae1-4ffb-b04b-e037ea351702` | 2560x1440 | `9759dc8a-1229-4fc5-a5f3-6b8fd37ad570/13f3a0f8-7ae1-4ffb-b04b-e037ea351702_galacticlegendary` |
| 33 | Galactic Legendary | `6021d3ad-cba4-4e2d-961a-decf6b08b7de` | 2560x1440 | `9759dc8a-1229-4fc5-a5f3-6b8fd37ad570/6021d3ad-cba4-4e2d-961a-decf6b08b7de_galacticlegendary` |
| 34 | Galactic Legendary | `b16fa22e-39c8-4ef6-9663-eb44e9c8697e` | 2560x1440 | `9759dc8a-1229-4fc5-a5f3-6b8fd37ad570/b16fa22e-39c8-4ef6-9663-eb44e9c8697e_galacticlegendary` |
| 35 | Galactic Legendary | `04583c64-4e43-4985-ad9f-31c2d2256a4d` | 1920x1080 | `97ca164f-a796-41af-bfc4-d5df2b1a7b95/04583c64-4e43-4985-ad9f-31c2d2256a4d_galacticlegendary` |
| 36 | Galactic Legendary | `2b95e397-ba91-4046-a4de-debf34fd24c8` | 1920x1080 | `97ca164f-a796-41af-bfc4-d5df2b1a7b95/2b95e397-ba91-4046-a4de-debf34fd24c8_galacticlegendary` |
| 37 | Galactic Legendary | `410eb3cb-df16-41d7-beb8-7240ed0b7d27` | 1920x1080 | `97ca164f-a796-41af-bfc4-d5df2b1a7b95/410eb3cb-df16-41d7-beb8-7240ed0b7d27_galacticlegendary` |
| 38 | Galactic Legendary | `494da8ba-d431-4298-9315-0b012d146224` | 1920x1080 | `97ca164f-a796-41af-bfc4-d5df2b1a7b95/494da8ba-d431-4298-9315-0b012d146224_galacticlegendary` |
| 39 | Galactic Legendary | `5235cf6f-c5e2-45f8-98e3-de1af946846f` | 1920x1080 | `97ca164f-a796-41af-bfc4-d5df2b1a7b95/5235cf6f-c5e2-45f8-98e3-de1af946846f_galacticlegendary` |
| 40 | Galactic Legendary | `662c9d3b-be31-4700-8b6a-054b7199948a` | 1920x1080 | `97ca164f-a796-41af-bfc4-d5df2b1a7b95/662c9d3b-be31-4700-8b6a-054b7199948a_galacticlegendary` |
| 41 | Galactic Legendary | `67fde203-17e4-419c-923c-43bb050d4966` | 1920x1080 | `97ca164f-a796-41af-bfc4-d5df2b1a7b95/67fde203-17e4-419c-923c-43bb050d4966_galacticlegendary` |
| 42 | Galactic Legendary | `6c35a860-cee5-44c3-b652-a674bf57a25f` | 1920x1080 | `97ca164f-a796-41af-bfc4-d5df2b1a7b95/6c35a860-cee5-44c3-b652-a674bf57a25f_galacticlegendary` |
| 43 | Galactic Legendary | `743721ff-a1b8-42cf-a855-3427fef35970` | 1920x1080 | `97ca164f-a796-41af-bfc4-d5df2b1a7b95/743721ff-a1b8-42cf-a855-3427fef35970_galacticlegendary` |
| 44 | Galactic Legendary | `7d69398f-4447-4ce2-90a7-403a95b6fcab` | 1920x1080 | `97ca164f-a796-41af-bfc4-d5df2b1a7b95/7d69398f-4447-4ce2-90a7-403a95b6fcab_galacticlegendary` |
| 45 | Galactic Legendary | `8bed3e51-4a15-40fb-b151-35bc4d9b234e` | 1920x1080 | `97ca164f-a796-41af-bfc4-d5df2b1a7b95/8bed3e51-4a15-40fb-b151-35bc4d9b234e_galacticlegendary` |
| 46 | Galactic Legendary | `ae2794d3-c34c-4fb8-b17a-52bda8b56bc7` | 1920x1080 | `97ca164f-a796-41af-bfc4-d5df2b1a7b95/ae2794d3-c34c-4fb8-b17a-52bda8b56bc7_galacticlegendary` |
| 47 | Galactic Legendary | `c6553178-89f5-43ce-b052-0d2cfef69405` | 1920x1080 | `97ca164f-a796-41af-bfc4-d5df2b1a7b95/c6553178-89f5-43ce-b052-0d2cfef69405_galacticlegendary` |
| 48 | Galactic Legendary | `cb6e5c91-d203-4e54-b41f-c46b15cf40f1` | 1920x1080 | `97ca164f-a796-41af-bfc4-d5df2b1a7b95/cb6e5c91-d203-4e54-b41f-c46b15cf40f1_galacticlegendary` |
| 49 | Galactic Legendary | `e6f88fd5-de86-4894-8e70-b20fc34483bd` | 1920x1080 | `97ca164f-a796-41af-bfc4-d5df2b1a7b95/e6f88fd5-de86-4894-8e70-b20fc34483bd_galacticlegendary` |
| 50 | Galactic Legendary | `f408421b-3e1c-4cde-925e-78b8bb1936f7` | 1920x1080 | `97ca164f-a796-41af-bfc4-d5df2b1a7b95/f408421b-3e1c-4cde-925e-78b8bb1936f7_galacticlegendary` |
| 51 | Galactic Legendary | `45d4e3b6-98f8-4c57-9279-08fa1a9f0206` | 3440x1440 | `b2733181-fe4f-4b3a-af34-24c424b87989/45d4e3b6-98f8-4c57-9279-08fa1a9f0206_galacticlegendary` |
| 52 | Galactic Legendary | `7a523419-c12f-4932-ad8e-bc3453c97e9d` | 3440x1440 | `b2733181-fe4f-4b3a-af34-24c424b87989/7a523419-c12f-4932-ad8e-bc3453c97e9d_galacticlegendary` |
| 53 | Galactic Legendary | `afa32eba-9e92-4609-a75e-8423c05e8fb8` | 3440x1440 | `b2733181-fe4f-4b3a-af34-24c424b87989/afa32eba-9e92-4609-a75e-8423c05e8fb8_galacticlegendary` |
| 54 | Galactic Legendary | `6d7d14cf-0052-421e-8dd8-3c7efefc0499` | 2560x1440 | `d1f4cb88-6a69-471a-8458-b251d8a268ea/6d7d14cf-0052-421e-8dd8-3c7efefc0499_galacticlegendary` |
| 55 | Galactic Legendary | `731ed317-af23-4c71-afad-dcf9abbaa11e` | 2560x1440 | `d1f4cb88-6a69-471a-8458-b251d8a268ea/731ed317-af23-4c71-afad-dcf9abbaa11e_galacticlegendary` |
| 56 | Galactic Legendary | `803d01fc-54e6-4744-948f-7d28647dafbf` | 2560x1440 | `d1f4cb88-6a69-471a-8458-b251d8a268ea/803d01fc-54e6-4744-948f-7d28647dafbf_galacticlegendary` |
| 57 | Galactic Legendary | `d862ba4e-4943-4f06-9058-19daafb2623f` | 2560x1440 | `d1f4cb88-6a69-471a-8458-b251d8a268ea/d862ba4e-4943-4f06-9058-19daafb2623f_galacticlegendary` |
| 58 | Galactic Legendary | `f28ebad1-16af-451d-88c1-5aef4b601b2b` | 2560x1440 | `d1f4cb88-6a69-471a-8458-b251d8a268ea/f28ebad1-16af-451d-88c1-5aef4b601b2b_galacticlegendary` |
| 59 | Galactic Legendary | `fa41055c-7742-4d21-9674-08a30711a4af` | 2560x1440 | `d1f4cb88-6a69-471a-8458-b251d8a268ea/fa41055c-7742-4d21-9674-08a30711a4af_galacticlegendary` |
| 60 | Galactic Legendary | `7c89daf9-832f-419f-a1d7-f308a4027639` | 2560x1440 | `f6b1cf32-4b54-4245-8aad-2ac4c158f94f/7c89daf9-832f-419f-a1d7-f308a4027639_galacticlegendary` |
| 61 | Galactic Legendary | `8fc00eb8-1710-4ace-99ca-34dfb37724e0` | 2560x1440 | `f6b1cf32-4b54-4245-8aad-2ac4c158f94f/8fc00eb8-1710-4ace-99ca-34dfb37724e0_galacticlegendary` |
| 62 | Galactic Mythic | `c5e2ba9f-72d4-48ae-a1fa-48542f9431cd` | 1440x1080 | `2dae5144-2a4c-4e05-ae33-a66116c02ca6/c5e2ba9f-72d4-48ae-a1fa-48542f9431cd_galacticmythic` |
| 63 | Galactic Mythic | `2dc03dea-658f-485b-b38d-61a866f69dfe` | 2560x1440 | `d1f4cb88-6a69-471a-8458-b251d8a268ea/2dc03dea-658f-485b-b38d-61a866f69dfe_galacticmythic` |
| 64 | Galactic Mythic | `63fedfeb-dd58-47f8-9bef-6599b7df1bb9` | 2560x1440 | `d1f4cb88-6a69-471a-8458-b251d8a268ea/63fedfeb-dd58-47f8-9bef-6599b7df1bb9_galacticmythic` |
| 65 | Galactic Mythic | `6e15b20f-0086-45f4-b28f-8c4d778e064d` | 2560x1440 | `d1f4cb88-6a69-471a-8458-b251d8a268ea/6e15b20f-0086-45f4-b28f-8c4d778e064d_galacticmythic` |
| 66 | Galactic Mythic | `7660c785-63e6-4457-bd5a-75b2c4a1a89a` | 2560x1440 | `d1f4cb88-6a69-471a-8458-b251d8a268ea/7660c785-63e6-4457-bd5a-75b2c4a1a89a_galacticmythic` |
| 67 | Galactic Mythic | `cfcc4a45-aa78-49a2-aac2-152782bce2c9` | 2560x1440 | `d1f4cb88-6a69-471a-8458-b251d8a268ea/cfcc4a45-aa78-49a2-aac2-152782bce2c9_galacticmythic` |
| 68 | Galactic Mythic | `6a8cba37-b8f7-49ab-91bf-1e841f7de154` | 2560x1440 | `f6b1cf32-4b54-4245-8aad-2ac4c158f94f/6a8cba37-b8f7-49ab-91bf-1e841f7de154_galacticmythic` |
| 69 | Galactic Rare | `1482a3d0-2fdb-4640-86b2-a237fe0b6ee1` | 2560x1080 | `6b9648fe-5ecc-46ac-b33b-854556290b46/1482a3d0-2fdb-4640-86b2-a237fe0b6ee1_galacticrare` |
| 70 | Galactic Rare | `2e6b86df-11a1-46a9-b91d-012b118327c9` | 2560x1080 | `6b9648fe-5ecc-46ac-b33b-854556290b46/2e6b86df-11a1-46a9-b91d-012b118327c9_galacticrare` |
| 71 | Galactic Rare | `418aa646-2719-4aa3-8a65-b95c7d1f5072` | 2560x1080 | `6b9648fe-5ecc-46ac-b33b-854556290b46/418aa646-2719-4aa3-8a65-b95c7d1f5072_galacticrare` |
| 72 | Galactic Rare | `4e0ea302-95e5-428e-989a-7fd2b92de8e6` | 2560x1080 | `6b9648fe-5ecc-46ac-b33b-854556290b46/4e0ea302-95e5-428e-989a-7fd2b92de8e6_galacticrare` |
| 73 | Galactic Rare | `624fbc77-e074-4b08-8509-d3080e850899` | 2560x1080 | `6b9648fe-5ecc-46ac-b33b-854556290b46/624fbc77-e074-4b08-8509-d3080e850899_galacticrare` |
| 74 | Galactic Rare | `74600c27-e053-4941-8a69-ff90f68714f8` | 2560x1080 | `6b9648fe-5ecc-46ac-b33b-854556290b46/74600c27-e053-4941-8a69-ff90f68714f8_galacticrare` |
| 75 | Galactic Rare | `90c6753a-1ac3-4ecf-b8b7-c95db227c2b1` | 2560x1080 | `6b9648fe-5ecc-46ac-b33b-854556290b46/90c6753a-1ac3-4ecf-b8b7-c95db227c2b1_galacticrare` |
| 76 | Galactic Rare | `c1673ca0-9b06-4882-9792-b27815de91cf` | 2560x1080 | `6b9648fe-5ecc-46ac-b33b-854556290b46/c1673ca0-9b06-4882-9792-b27815de91cf_galacticrare` |

### Rainbow false detections (7)

| # | Recorded target | Submission | Resolution | Folder |
|---:|---|---|---:|---|
| 1 | Rainbow Mythic | `179a299b-1e8d-4794-ba44-d899b44a6b15` | 1920x1080 | `2769a23a-9a83-4d4d-802f-2be74b0c8510/179a299b-1e8d-4794-ba44-d899b44a6b15_rainbowmythic` |
| 2 | Rainbow Mythic | `7538adf1-6129-411c-99ef-d3bd2b3ab440` | 1920x1080 | `2769a23a-9a83-4d4d-802f-2be74b0c8510/7538adf1-6129-411c-99ef-d3bd2b3ab440_rainbowmythic` |
| 3 | Rainbow Mythic | `9678169d-42d2-47a7-81c7-70b5d03c86e4` | 1440x1080 | `2dae5144-2a4c-4e05-ae33-a66116c02ca6/9678169d-42d2-47a7-81c7-70b5d03c86e4_rainbowmythic` |
| 4 | Rainbow Mythic | `28706334-4ab0-4838-bf58-4a11e60fc41f` | 1920x1080 | `97ca164f-a796-41af-bfc4-d5df2b1a7b95/28706334-4ab0-4838-bf58-4a11e60fc41f_rainbowmythic` |
| 5 | Rainbow Mythic | `57d2bb00-da25-4fa5-b15d-d2c5892c966b` | 1920x1080 | `97ca164f-a796-41af-bfc4-d5df2b1a7b95/57d2bb00-da25-4fa5-b15d-d2c5892c966b_rainbowmythic` |
| 6 | Rainbow Mythic | `684592fe-cd76-4e19-be1c-be63688e1ff4` | 1920x1080 | `97ca164f-a796-41af-bfc4-d5df2b1a7b95/684592fe-cd76-4e19-be1c-be63688e1ff4_rainbowmythic` |
| 7 | Rainbow Mythic | `cf56e080-91af-45b1-916d-3b531907d213` | 2560x1440 | `d1f4cb88-6a69-471a-8458-b251d8a268ea/cf56e080-91af-45b1-916d-3b531907d213_rainbowmythic` |

## Uncertain detections requiring human review

| Recorded target | Submission | Resolution | Folder |
|---|---|---:|---|
| Beskar Epic | `18b8e113-268e-44fa-9420-dac143ee44f1` | 2560x1440 | `f6b1cf32-4b54-4245-8aad-2ac4c158f94f/18b8e113-268e-44fa-9420-dac143ee44f1_beskarepic` |
| Beskar Legendary | `7b7b726b-0342-4a5f-8644-9c10c457678f` | 2560x1440 | `d1f4cb88-6a69-471a-8458-b251d8a268ea/7b7b726b-0342-4a5f-8644-9c10c457678f_beskarlegendary` |

## Verification use

```bash
PYTHONPATH=src python3 tests/run_debug_batch_eval.py '/Users/rubenvancraenenbroeck/Downloads/data 2'
```

The evaluator requires every real target to remain detected, every false target to be absent, and skips uncertain rows. The 106 Diamond Mythic submissions remain outside this report's requested scope.
