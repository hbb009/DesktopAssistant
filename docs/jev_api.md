# Jev 决策模型接入指南（OpenRouter Decisions API）

Jev（`typesafe/jev-1.13`，别名 `~typesafe/jev-latest`）是 TypeSafe 的**结构化决策模型**，不是聊天模型。  
不要调用 `/api/v1/chat/completions`，也不要再使用已失效的模型 id `typesafe/jev`。

正确入口：**`POST https://openrouter.ai/api/alpha/decisions`**，请求体为 `{ model, state, questions }`，读取响应里的 `answers`。

桌面助手「游戏大脑」点「开工」在 JEV 模式下走本路径；其它模型仍走 OpenAI 兼容 chat/completions。（全局 F7 已改为「静默复制」：按侧栏复制归属推送前台选区，可在系统总览热键卡关闭，不再触发开工。）

## 步骤 1：请求头

```javascript
const headers = {
  "Authorization": "Bearer sk-or-v1-…",  // OpenRouter API Key
  "Content-Type": "application/json",
  // 可选：便于在 OpenRouter 后台区分来源（与其它 OR 调用保持一致即可）
  "HTTP-Referer": "http://localhost:3000",
  "X-Title": "My Desktop Game Assistant"
};
```

## 步骤 2：请求体（state + Choice 问题）

`state` 放游戏画面/识别文字（字符串或对象均可；本助手用 `{ "screen": "…" }`）。  
`questions` 里放一个名为 `action` 的 **choice**：`criteria` 的 key 建议用 ASCII（如 `opt_1`），中文选项放在 value，再把返回的 choice key 映射回中文。

```javascript
const body = {
  model: "typesafe/jev-1.13",  // 勿用 typesafe/jev
  state: {
    screen: "当前游戏画面识别结果：玩家血量25%，前方有两只哥布林。\n1. 吃红药\n2. 放技能A\n3. 逃跑"
  },
  questions: {
    action: {
      type: "choice",
      instructions: "玩家此刻应采取的动作或选择",
      criteria: {
        opt_1: "1. 吃红药",
        opt_2: "2. 放技能A",
        opt_3: "3. 逃跑"
      }
    }
  }
};
```

画面里抽不到明确选项时，可用通用动作兜底，例如：`continue` / `wait` / `attack` / `defend` / `heal` / `flee`（value 写中文说明）。

## 步骤 3：发送并解析 `answers`

```javascript
async function getJevDecision() {
  const response = await fetch("https://openrouter.ai/api/alpha/decisions", {
    method: "POST",
    headers,
    body: JSON.stringify(body)
  });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${await response.text()}`);
  }
  const data = await response.json();
  // data.answers.action.choice → "opt_1" 等；另有 confidence、probabilities
  const choiceKey = data.answers.action.choice;
  const label = body.questions.action.criteria[choiceKey] || choiceKey;
  console.log("Jev 决策：", label, data.answers.action.confidence);
  // TODO: 交给自动化 / 展示到「回答」区
}
```

## 对照（避免回退）

| 项目 | 错误（旧） | 正确（现） |
|------|------------|------------|
| 模型 id | `typesafe/jev` | `typesafe/jev-1.13` 或 `~typesafe/jev-latest` |
| 端点 | `POST …/api/v1/chat/completions` | `POST …/api/alpha/decisions` |
| 输出 | `choices[0].message.content` + json_schema | `answers.action.choice`（+ confidence/probabilities） |

更多说明见 OpenRouter：[Jev 文档](https://openrouter.ai/docs/guides/community/jev)、[Decisions API](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request)。
