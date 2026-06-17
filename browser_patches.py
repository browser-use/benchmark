"""Runtime browser-use patches for remote browser benchmark providers."""

import asyncio
from typing import Any


_REMOTE_TYPE_PATCH_INSTALLED = False


def install_remote_typing_fallback(timeout: float = 20.0) -> None:
    """Bound browser-use typing and fall back to direct DOM assignment.

    browser-use types text through many low-level CDP key events. On remote CDP
    providers a single key event can hang long enough to stall the agent. This
    patch keeps browser-use's normal typing path first, but gives it a bounded
    window before setting the target element value directly and dispatching the
    DOM events frameworks expect.
    """

    global _REMOTE_TYPE_PATCH_INSTALLED
    if _REMOTE_TYPE_PATCH_INSTALLED:
        return

    from browser_use.browser.watchdogs.default_action_watchdog import (
        DefaultActionWatchdog,
    )

    original_on_type = DefaultActionWatchdog.on_TypeTextEvent

    async def _set_text_directly(self: Any, event: Any) -> dict | None:
        element_node = event.node
        backend_node_id = element_node.backend_node_id
        if not backend_node_id:
            await self._type_to_page(event.text)
            return None

        cdp_session = await self.browser_session.cdp_client_for_node(element_node)
        result = await self.browser_session.cdp_client.send.DOM.resolveNode(
            params={"backendNodeId": backend_node_id},
            session_id=cdp_session.session_id,
        )
        object_id = result["object"]["objectId"]

        value_script = """
            function(newValue, shouldClear) {
                const element = this;
                if (!element) return null;

                element.focus();
                const nextValue = shouldClear
                    ? newValue
                    : ((element.value !== undefined ? element.value : element.textContent) || '') + newValue;

                if (element.value !== undefined) {
                    const proto = element instanceof HTMLTextAreaElement
                        ? HTMLTextAreaElement.prototype
                        : HTMLInputElement.prototype;
                    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                    if (desc && desc.set) {
                        desc.set.call(element, nextValue);
                    } else {
                        element.value = nextValue;
                    }
                } else if (element.isContentEditable) {
                    element.textContent = nextValue;
                } else {
                    element.textContent = nextValue;
                }

                element.dispatchEvent(new InputEvent('input', {
                    bubbles: true,
                    cancelable: true,
                    data: newValue,
                    inputType: 'insertText'
                }));
                element.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
                return element.value !== undefined ? element.value : element.textContent;
            }
        """
        set_result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(
            params={
                "objectId": object_id,
                "functionDeclaration": value_script,
                "arguments": [
                    {"value": event.text},
                    {"value": bool(event.clear or not event.text)},
                ],
                "returnByValue": True,
            },
            session_id=cdp_session.session_id,
        )

        metadata: dict[str, Any] = {"typing_fallback": "direct_dom"}
        if not event.is_sensitive:
            metadata["actual_value"] = set_result.get("result", {}).get("value")
        return metadata

    async def patched_on_type(self: Any, event: Any) -> dict | None:
        try:
            return await asyncio.wait_for(original_on_type(self, event), timeout=timeout)
        except asyncio.TimeoutError:
            node = event.node
            index_for_logging = node.backend_node_id or "unknown"
            self.logger.warning(
                "TypeTextEvent exceeded %.1fs for element %s; using direct DOM fallback.",
                timeout,
                index_for_logging,
            )
            return await _set_text_directly(self, event)

    patched_on_type.__name__ = "on_TypeTextEvent"
    DefaultActionWatchdog.on_TypeTextEvent = patched_on_type
    _REMOTE_TYPE_PATCH_INSTALLED = True
