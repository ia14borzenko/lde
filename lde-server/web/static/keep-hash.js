/* Страница PIN: после отправки формы часть ссылки после # (ключ) теряется — сохраняем её для этой вкладки. */
try { if (location.hash.length > 1) sessionStorage.setItem("lde-key-pending", location.hash.slice(1)); } catch (e) { /* */ }
