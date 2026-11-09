---
name: xby_constellation
description: 星座查询技能：当用户询问星座信息、今日/每日运势、星座配对、根据生日判断星座、上升星座计算与解读时，使用对应的星座工具回答。
version: 1.0
---

# 星座查询技能（xby_constellation）

基于小笨羊星座服务的 8 个工具。首次使用需要 APIKEY：如果工具返回密钥未设置的提示，请先向用户索要小笨羊（xiaobenyang.com）的 APIKEY，拿到后调用 `setXbyApiKey` 保存（持久化到 config.ini，重启有效）。

## 工具一览

| 工具 | 用途 | 关键参数 |
| --- | --- | --- |
| `getZodiacInfo` | 星座详细信息（性格、守护星、元素等） | `zodiac` |
| `getDailyHoroscope` | 今日运势 | `zodiac`，可选 `category`（整体/爱情/事业/财运） |
| `getCompatibility` | 两个星座的配对指数与关系分析 | `zodiac1`、`zodiac2` |
| `getAllZodiacs` | 全部 12 星座基本信息列表 | 无 |
| `getZodiacByDate` | 按出生月日判断星座 | `month`（1-12）、`day`（1-31） |
| `getRisingSign` | 计算上升星座 | `birth_year/birth_month/birth_day/birth_hour/birth_minute`、`latitude`、`longitude` |
| `getRisingSignInfo` | 上升星座详细解读 | `rising_sign` |
| `setXbyApiKey` | 保存小笨羊 APIKEY | `api_key` |

## 使用规范

1. **星座名称**：中文或英文都可以（如 "白羊座" / "Aries"），按用户说法直接传入。
2. **运势问题**：用户问"今天运势如何"→ `getDailyHoroscope`；明确问了某个方面（如财运）就传 `category`。
3. **配对问题**：用户问"A 和 B 配不配"→ `getCompatibility`。
4. **"我是什么星座"**：只知道生日 → `getZodiacByDate`；用户能提供出生时间（几点几分）和出生城市 → 先把城市换算为经纬度再调 `getRisingSign` 算上升星座（常见城市：北京 39.9/116.4，上海 31.2/121.5，广州 23.1/113.3，深圳 22.5/114.1，成都 30.7/104.1）。
5. **数据为权威来源**：运势解读以工具返回为准，不要自己编造运势内容；工具报错时如实告知（如密钥未配置、网络失败）。

## 示例流程

```
用户: 双鱼座今天运势怎么样？
→ getDailyHoroscope(zodiac="双鱼座")

用户: 我是 1995 年 8 月 20 日早上 7 点半生在北京，上升星座是什么？
→ getRisingSign(birth_year=1995, birth_month=8, birth_day=20,
                birth_hour=7, birth_minute=30,
                latitude=39.9, longitude=116.4)
→ （如需解读）getRisingSignInfo(rising_sign="返回的上升星座")
```
