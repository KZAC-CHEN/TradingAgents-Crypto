"""CLI 与程序接口共用的报告目录写入器。"""

from datetime import datetime
from pathlib import Path
from typing import Any


def _build_decision_summary(
    final_state: dict[str, Any],
    ticker: str,
    *,
    signal: str | None,
    evidence_health: dict[str, Any] | None,
) -> str:
    """把组合结论、执行计划和证据健康状态整理为报告首页摘要。"""
    risk = final_state.get("risk_debate_state") or {}
    decision = (
        risk.get("judge_decision")
        or final_state.get("final_trade_decision")
        or final_state.get("trader_investment_plan")
        or "组合经理没有生成最终结论。"
    )
    health = evidence_health or {}
    health_state = str(health.get("state") or "unknown")
    health_label = {
        "ok": "完整",
        "degraded": "降级",
        "unknown": "未记录",
    }.get(health_state, health_state)
    failed_sections = [str(item) for item in health.get("failed_sections") or []]
    provider_issues = [
        str(item.get("provider") or "未知来源")
        for item in health.get("provider_issues") or []
        if isinstance(item, dict)
    ]
    lines = [
        f"# {ticker} 最终投资建议",
        "",
        f"- **最终信号：{signal or '请查看组合经理结论'}**",
        f"- **证据状态：{health_label}**",
    ]
    if failed_sections:
        lines.append(f"- **失败分区：{'、'.join(failed_sections)}**")
    if provider_issues:
        lines.append(f"- **异常来源：{'、'.join(dict.fromkeys(provider_issues))}**")
    lines.extend(
        [
            "",
            "## 组合经理结论",
            "",
            str(decision),
        ]
    )
    return "\n".join(lines).strip() + "\n"


def write_report_tree(
    final_state: dict[str, Any],
    ticker: str,
    save_path,
    *,
    signal: str | None = None,
    evidence_health: dict[str, Any] | None = None,
) -> Path:
    """保存一次运行的分项报告，并返回完整报告路径。"""
    save_path = Path(save_path)
    save_path.mkdir(parents=True, exist_ok=True)
    sections = []

    decision_summary = _build_decision_summary(
        final_state,
        ticker,
        signal=signal,
        evidence_health=evidence_health,
    )

    # 1. 分析师报告
    analysts_dir = save_path / "1_analysts"
    analyst_parts = []
    if final_state.get("market_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "market.md").write_text(final_state["market_report"], encoding="utf-8")
        analyst_parts.append(("Market Analyst", final_state["market_report"]))
    if final_state.get("sentiment_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "sentiment.md").write_text(final_state["sentiment_report"], encoding="utf-8")
        analyst_parts.append(("Sentiment Analyst", final_state["sentiment_report"]))
    if final_state.get("news_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "news.md").write_text(final_state["news_report"], encoding="utf-8")
        analyst_parts.append(("News Analyst", final_state["news_report"]))
    if final_state.get("fundamentals_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "fundamentals.md").write_text(final_state["fundamentals_report"], encoding="utf-8")
        analyst_parts.append(("Fundamentals Analyst", final_state["fundamentals_report"]))
    if analyst_parts:
        content = "\n\n".join(f"### {name}\n{text}" for name, text in analyst_parts)
        sections.append(f"## I. Analyst Team Reports\n\n{content}")

    # 2. 研究团队报告
    if final_state.get("investment_debate_state"):
        research_dir = save_path / "2_research"
        debate = final_state["investment_debate_state"]
        research_parts = []
        if debate.get("bull_history"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "bull.md").write_text(debate["bull_history"], encoding="utf-8")
            research_parts.append(("Bull Researcher", debate["bull_history"]))
        if debate.get("bear_history"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "bear.md").write_text(debate["bear_history"], encoding="utf-8")
            research_parts.append(("Bear Researcher", debate["bear_history"]))
        if debate.get("judge_decision"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "manager.md").write_text(debate["judge_decision"], encoding="utf-8")
            research_parts.append(("Research Manager", debate["judge_decision"]))
        if research_parts:
            content = "\n\n".join(f"### {name}\n{text}" for name, text in research_parts)
            sections.append(f"## II. Research Team Decision\n\n{content}")

    # 3. 交易计划
    if final_state.get("trader_investment_plan"):
        trading_dir = save_path / "3_trading"
        trading_dir.mkdir(exist_ok=True)
        (trading_dir / "trader.md").write_text(final_state["trader_investment_plan"], encoding="utf-8")
        sections.append(f"## III. Trading Team Plan\n\n### Trader\n{final_state['trader_investment_plan']}")

    # 4. 风险管理报告
    risk = final_state.get("risk_debate_state") or {}
    if risk:
        risk_dir = save_path / "4_risk"
        risk_parts = []
        if risk.get("aggressive_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "aggressive.md").write_text(risk["aggressive_history"], encoding="utf-8")
            risk_parts.append(("Aggressive Analyst", risk["aggressive_history"]))
        if risk.get("conservative_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "conservative.md").write_text(risk["conservative_history"], encoding="utf-8")
            risk_parts.append(("Conservative Analyst", risk["conservative_history"]))
        if risk.get("neutral_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "neutral.md").write_text(risk["neutral_history"], encoding="utf-8")
            risk_parts.append(("Neutral Analyst", risk["neutral_history"]))
        if risk_parts:
            content = "\n\n".join(f"### {name}\n{text}" for name, text in risk_parts)
            sections.append(f"## IV. Risk Management Team Decision\n\n{content}")

    if risk.get("judge_decision") or final_state.get("final_trade_decision"):
        portfolio_dir = save_path / "5_portfolio"
        portfolio_dir.mkdir(exist_ok=True)
        (portfolio_dir / "decision.md").write_text(decision_summary, encoding="utf-8")

    # 完整报告把可执行结论放在最前面，后面保留全部推理链条供审计。
    header = f"# Trading Analysis Report: {ticker}\n\nGenerated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    summary_body = decision_summary.partition("\n\n")[2] or decision_summary
    complete = header + "## 执行摘要\n\n" + summary_body + "\n\n" + "\n\n".join(sections)
    (save_path / "complete_report.md").write_text(complete, encoding="utf-8")
    return save_path / "complete_report.md"
