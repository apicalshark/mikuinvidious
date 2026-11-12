(function () {
  var chatContainer = document.getElementById("live-chat-container");
  if (!chatContainer || !chatContainer.dataset.vid) return;

  var vid = chatContainer.dataset.vid;
  var eventSource;

  function connectChat() {
    if (eventSource) eventSource.close();
    eventSource = new EventSource("/live/chat/" + encodeURIComponent(vid));

    eventSource.onmessage = function (event) {
      var data = JSON.parse(event.data);

      var loadingMsg = chatContainer.querySelector(".text-center");
      if (loadingMsg) chatContainer.innerHTML = "";

      var isAtBottom =
        chatContainer.scrollHeight - chatContainer.clientHeight <= chatContainer.scrollTop + 50;

      var msgEl = document.createElement("div");
      msgEl.className =
        "py-1 px-2 hover:bg-surface-variant/10 transition-colors rounded break-words break-all";

      var userSpan = document.createElement("span");
      userSpan.className = "font-bold text-primary mr-1 whitespace-nowrap";
      userSpan.textContent = data.user + ":";

      var textSpan = document.createElement("span");
      textSpan.className = "text-on-surface opacity-90";
      textSpan.textContent = data.text;

      msgEl.appendChild(userSpan);
      msgEl.appendChild(textSpan);
      chatContainer.appendChild(msgEl);

      if (window.dm && window.dm_status) {
        window.dm.emit({
          text: "\n" + data.text,
          mode: "rtl",
          style: {
            fontSize: "25px",
            color: "#ffffff",
            textShadow: "-1px -1px #000, -1px 1px #000, 1px -1px #000, 1px 1px #000",
            font: "25px sans-serif",
            whiteSpace: "pre",
          },
        });
      }

      if (isAtBottom) {
        chatContainer.scrollTop = chatContainer.scrollHeight;
      }

      if (chatContainer.children.length > 200) {
        chatContainer.removeChild(chatContainer.firstChild);
      }
    };

    eventSource.onerror = function () {
      eventSource.close();
      setTimeout(connectChat, 5000);
    };
  }

  connectChat();
})();
