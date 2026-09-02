# Step 01：范围和任务契约

## 目标

在 `活文档/01-范围与任务契约.md` 中，用自然语言建立任务上下文。

## 本步骤允许写入

- `活文档/01-范围与任务契约.md`
- `内部索引/运行状态.json`
- `内部索引/运行计划.json`
- `内部索引/输入材料索引.json`
- `内部索引/方法论选择.json`：逐项记录内置/用户方法论的 selected 或 excluded、原因和证据。

方法论收据必须是如下语义结构（不把它当作分析正文）：

```json
{
  "schema_version": "1.0",
  "selected": [{"methodology_id": "codetalks-skill", "reason": "内置方法始终启用", "evidence": ["target", "source_scope"]}],
  "excluded": []
}
```

用户方法论 ID 只能来自 `inputs/methodologies/catalog.json`；不得凭名称猜测或写入冻结目录之外的 ID。

## 禁止

- 写入 `正式输出/`
- 在运行根目录散落 `01-*.md`
- 创建 `活文档/活文档/`

正式输出要到 Step 09 才生成。
