"""SMTP 邮件推送（FR-08）。

邮件正文是**自包含 HTML**：只能用内联样式。邮件客户端大多会剥离 `<style>`
与外链 CSS，写成一坨 class 在最需要好看的场景里反而全丢。

一个刻意的取舍：发信失败不抛异常，而是返回 `NotifyResult(ok=False, detail=...)`。
调用方要把失败原因存进报告，让界面显示「上次推送失败」——异常会让这条信息
止步于日志里，用户什么也看不到。
"""

from __future__ import annotations

import logging
import smtplib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate

from shared.domain.models.entities import ReviewReport

logger = logging.getLogger(__name__)

# 邮件里不引用外链资源：离线打开也要能看
_FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif"


@dataclass(slots=True)
class NotifyResult:
    """推送结果。失败不抛异常，原因放在 detail 里让界面能显示。"""

    ok: bool
    detail: str = ""


class Notifier(ABC):
    """推送端口。目前只有 SMTP 一种实现，保留接口是为了让调用方不关心渠道。"""

    name: str = "base"

    @abstractmethod
    def send_report(self, report: ReviewReport, summary_html: str) -> NotifyResult:
        """把回顾报告推送给用户。"""


def _esc(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _metric_card(label: str, value: str, hint: str = "") -> str:
    hint_html = (
        f'<div style="font-size:12px;color:#8a8f98;margin-top:4px;">{_esc(hint)}</div>'
        if hint
        else ""
    )
    return (
        '<td style="padding:12px 14px;background:#f6f7f9;border-radius:10px;'
        'vertical-align:top;width:50%;">'
        f'<div style="font-size:12px;color:#6b7280;">{_esc(label)}</div>'
        f'<div style="font-size:22px;font-weight:600;color:#111827;margin-top:2px;">{_esc(value)}</div>'
        f"{hint_html}</td>"
    )


def build_report_html(report: ReviewReport, link: str = "http://localhost:5173/review") -> str:
    """把报告的 metrics 渲染成邮件正文。

    只取关键数字与错题 TOP5，不把整份 Markdown 塞进邮件——邮件是「提醒你去看」，
    不是阅读器。正文太长在手机上会被折叠成一片空白。
    """
    metrics = report.metrics or {}
    documents = metrics.get("documents", {})
    practice = metrics.get("practice", {})
    wrong = metrics.get("wrong", {})
    top = wrong.get("top", [])[:5]
    period = metrics.get("period", {})

    cards = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        "<tr>"
        + _metric_card("新增资料", f"{documents.get('total', 0)} 份", f"{documents.get('chunks', 0)} 个片段")
        + '<td style="width:12px;"></td>'
        + _metric_card("完成练习", f"{practice.get('sessions', 0)} 套", f"{practice.get('answered', 0)} 题")
        + "</tr><tr><td colspan=\"3\" style=\"height:12px;\"></td></tr><tr>"
        + _metric_card("正确率", f"{practice.get('accuracy', 0)}%", f"答对 {practice.get('correct', 0)} 题")
        + '<td style="width:12px;"></td>'
        + _metric_card("错题", f"{wrong.get('total', 0)} 道", f"重复错 {wrong.get('repeat_count', 0)} 道")
        + "</tr></table>"
    )

    if top:
        rows = "".join(
            f'<tr><td style="padding:10px 0;border-bottom:1px solid #eef0f3;">'
            f'<div style="font-size:14px;color:#111827;line-height:1.5;">{_esc(item["stem"])}</div>'
            f'<div style="font-size:12px;color:#8a8f98;margin-top:4px;">'
            f"错 {item['wrong_count']} 次 · {_esc(item.get('topic_name', '未分类'))}"
            f"</div></td></tr>"
            for item in top
        )
        wrong_block = (
            '<h2 style="font-size:15px;color:#111827;margin:26px 0 10px;">错得最多的题</h2>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table>'
        )
    else:
        wrong_block = (
            '<h2 style="font-size:15px;color:#111827;margin:26px 0 10px;">错题</h2>'
            '<p style="font-size:14px;color:#6b7280;margin:0;">这段周期没有新增错题。</p>'
        )

    weak = (metrics.get("wrong", {}) or {}).get("by_topic", [])[:3]
    weak_line = ""
    if weak:
        names = "、".join(_esc(item["name"]) for item in weak)
        weak_line = (
            '<p style="font-size:14px;color:#374151;line-height:1.7;margin:14px 0 0;">'
            f"错题集中在：{names}。完整分析与建议在应用里。</p>"
        )

    return f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#f3f4f6;font-family:{_FONT};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:28px 12px;">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border-radius:14px;padding:28px;">
<tr><td>
  <div style="font-size:12px;color:#8a8f98;letter-spacing:.04em;">A4S 学习回顾</div>
  <h1 style="font-size:20px;color:#111827;margin:6px 0 4px;font-weight:600;">最近 {report.window_days} 天的学习情况</h1>
  <div style="font-size:13px;color:#8a8f98;">{_esc(period.get("start", "")[:10])} → {_esc(period.get("end", "")[:10])}</div>
  <div style="height:20px;"></div>
  {cards}
  {weak_line}
  {wrong_block}
  <div style="margin-top:26px;">
    <a href="{link}" style="display:inline-block;background:#1f2937;color:#ffffff;text-decoration:none;
      font-size:14px;padding:11px 20px;border-radius:9px;">查看完整报告</a>
  </div>
  <p style="font-size:12px;color:#9ca3af;line-height:1.7;margin:20px 0 0;">
    这封邮件由本地运行的个人学习助手 A4S 发送，数据只存在你自己的电脑上。
  </p>
</td></tr>
</table>
</td></tr>
</table>
</body></html>"""


def build_test_html(host: str, to: str) -> str:
    return f"""<!DOCTYPE html>
<html><body style="margin:0;padding:24px;background:#f3f4f6;font-family:{_FONT};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;margin:0 auto;background:#ffffff;border-radius:14px;padding:26px;">
<tr><td>
  <h1 style="font-size:18px;color:#111827;margin:0 0 10px;font-weight:600;">SMTP 配置可用</h1>
  <p style="font-size:14px;color:#374151;line-height:1.7;margin:0;">
    收到这封邮件说明 A4S 能通过 <b>{_esc(host)}</b> 正常发信。
    之后每周的回顾报告会发到 <b>{_esc(to)}</b>。
  </p>
  <p style="font-size:12px;color:#9ca3af;margin:18px 0 0;">这是一封测试邮件，无需回复。</p>
</td></tr>
</table>
</body></html>"""


class SmtpNotifier(Notifier):
    name = "smtp"

    def __init__(
        self,
        *,
        host: str,
        port: int,
        use_ssl: bool,
        user: str,
        password: str,
        from_addr: str,
        to: str,
        timeout: int = 20,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.use_ssl = bool(use_ssl)
        self.user = user
        self.password = password
        self.from_addr = from_addr or user
        self.to = to
        self.timeout = timeout

    # ---- 内部 ----

    def _missing(self) -> str:
        if not self.host:
            return "未填写 SMTP 服务器"
        if not self.user or not self.password:
            return "未填写 SMTP 账号或授权码"
        if not self.to:
            return "未填写收件人"
        return ""

    def _send(self, subject: str, html: str) -> NotifyResult:
        missing = self._missing()
        if missing:
            return NotifyResult(ok=False, detail=missing)

        message = MIMEMultipart("alternative")
        message["Subject"] = Header(subject, "utf-8")
        message["From"] = formataddr((str(Header("A4S 学习助手", "utf-8")), self.from_addr))
        message["To"] = self.to
        message["Date"] = formatdate(localtime=True)
        message.attach(MIMEText(html, "html", "utf-8"))

        try:
            if self.use_ssl:
                server: smtplib.SMTP = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout)
            else:
                server = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
                server.starttls()
            try:
                server.login(self.user, self.password)
                server.sendmail(self.from_addr, [addr.strip() for addr in self.to.split(",") if addr.strip()], message.as_string())
            finally:
                server.quit()
        except smtplib.SMTPAuthenticationError:
            return NotifyResult(
                ok=False,
                detail="账号或授权码错误（多数邮箱需要使用「SMTP 授权码」，不是登录密码）",
            )
        except smtplib.SMTPRecipientsRefused:
            return NotifyResult(ok=False, detail=f"收件人被拒绝：{self.to}")
        except (smtplib.SMTPException, OSError) as exc:
            return NotifyResult(ok=False, detail=f"{type(exc).__name__}: {str(exc)[:160]}")

        return NotifyResult(ok=True, detail=f"已发送至 {self.to}")

    # ---- 端口 ----

    def send_report(self, report: ReviewReport, summary_html: str = "") -> NotifyResult:
        subject = f"A4S 学习回顾 · 最近 {report.window_days} 天"
        html = summary_html or build_report_html(report)
        return self._send(subject, html)

    def send_test(self) -> NotifyResult:
        return self._send("A4S SMTP 测试", build_test_html(self.host, self.to))


def build_notifier(values: dict | None = None) -> SmtpNotifier:
    """按配置构造通知器；`values` 用于"测试连接"时的临时覆盖。"""
    from shared.config import settings

    def pick(key: str):
        if values and key in values:
            return values[key]
        return getattr(settings, key)

    return SmtpNotifier(
        host=pick("smtp_host"),
        port=pick("smtp_port"),
        use_ssl=pick("smtp_use_ssl"),
        user=pick("smtp_user"),
        password=pick("smtp_password"),
        from_addr=pick("smtp_from"),
        to=pick("smtp_to"),
    )
