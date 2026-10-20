/** @odoo-module **/

const click = new URLSearchParams(window.location.search).get("vm_click");
if (/^[a-z0-9]{8,100}$/i.test(click || "")) {
    fetch("/netmasha/attribution", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            jsonrpc: "2.0",
            method: "call",
            params: { vm_click: click },
            id: Date.now(),
        }),
        keepalive: true,
    }).catch(() => undefined);
}
