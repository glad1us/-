<?php
// Помощник настройки бота MAX. Открыть: max-setup.php?key=ПАРОЛЬ_ИЗ_CONFIG
// 1) Проверяет токен бота.
// 2) Показывает, кто написал боту, и их chat_id — их нужно вписать в $MAX_CHAT_IDS.
// 3) Кнопка «Тест» отправляет пробную заявку во все чаты из настроек.

require __DIR__ . "/config.php";
require __DIR__ . "/max.php";

header("Content-Type: text/html; charset=UTF-8");

if ($SETUP_KEY === "" || !hash_equals($SETUP_KEY, (string)($_GET["key"] ?? ""))) {
    http_response_code(403);
    exit("Доступ закрыт. Укажите \$SETUP_KEY в config.php и откройте страницу с ?key=...");
}

function h($s) { return htmlspecialchars((string)$s, ENT_QUOTES, "UTF-8"); }

$out = [];

if ($MAX_BOT_TOKEN === "") {
    $out[] = "<p class=err>Не указан \$MAX_BOT_TOKEN в config.php.</p>";
} else {
    $me = max_request($MAX_BOT_TOKEN, "GET", "/me");
    if ($me["code"] !== 200) {
        $out[] = "<p class=err>Токен не подошёл (код " . h($me["code"]) . " " . h($me["error"]) . ").</p>";
    } else {
        $bot = $me["data"];
        $out[] = "<p class=ok>Бот подключён: <b>" . h($bot["name"] ?? $bot["first_name"] ?? "") . "</b> " . h(isset($bot["username"]) ? "@" . $bot["username"] : "") . "</p>";

        if (isset($_GET["test"])) {
            if (!$MAX_CHAT_IDS) {
                $out[] = "<p class=err>Список \$MAX_CHAT_IDS пуст — сначала впишите chat_id.</p>";
            }
            foreach ($MAX_CHAT_IDS as $id) {
                $ok = max_send($MAX_BOT_TOKEN, $id, "Тест: бот сайта АвтоОкей настроен. Сюда будут приходить заявки с сайта.");
                $out[] = "<p class=" . ($ok ? "ok" : "err") . ">chat_id " . h($id) . ": " . ($ok ? "отправлено" : "не удалось") . "</p>";
            }
        }

        $upd = max_request($MAX_BOT_TOKEN, "GET", "/updates", ["limit" => 100, "timeout" => 0]);
        $chats = [];
        foreach (($upd["data"]["updates"] ?? []) as $u) {
            $m = $u["message"] ?? null;
            $chatId = $m["recipient"]["chat_id"] ?? $u["chat_id"] ?? null;
            $user   = $m["sender"] ?? $u["user"] ?? [];
            if ($chatId === null) continue;
            $chats[$chatId] = [
                "name" => trim(($user["first_name"] ?? $user["name"] ?? "") . " " . ($user["last_name"] ?? "")),
                "user_id" => $user["user_id"] ?? "",
                "text" => $m["body"]["text"] ?? ($u["update_type"] ?? ""),
            ];
        }
        if (!$chats) {
            $out[] = "<p>Пока никто не писал боту. Найдите бота в MAX, нажмите «Начать» или напишите ему любое сообщение и обновите эту страницу.</p>";
        } else {
            $rows = "";
            foreach ($chats as $id => $c) {
                $rows .= "<tr><td>" . h($c["name"]) . "</td><td><b>" . h($id) . "</b></td><td>" . h($c["text"]) . "</td></tr>";
            }
            $out[] = "<p>Кто написал боту — впишите нужные chat_id в \$MAX_CHAT_IDS в config.php:</p><table><tr><th>Имя</th><th>chat_id</th><th>Сообщение</th></tr>$rows</table>";
        }
        $out[] = "<p><a href='?key=" . h(urlencode($SETUP_KEY)) . "&test=1'>Отправить тестовую заявку</a></p>";
    }
}
?>
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta name="robots" content="noindex" />
  <title>Настройка бота MAX</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 760px; margin: 30px auto; padding: 0 16px; line-height: 1.5; }
    table { border-collapse: collapse; width: 100%; }
    td, th { border: 1px solid #ccc; padding: 6px 10px; text-align: left; }
    .ok { color: #137333; } .err { color: #c5221f; }
  </style>
</head>
<body>
  <h1>Настройка бота MAX</h1>
  <?= implode("\n", $out) ?>
  <p><small>После настройки очистите $SETUP_KEY в config.php — страница закроется.</small></p>
</body>
</html>
