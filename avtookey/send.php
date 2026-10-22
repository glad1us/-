<?php
// Обработчик заявки с сайта АвтоОкей.
// Отправляет заявку в Telegram (если указан бот) и/или на почту.

// ===== Настройки =====
$MAIL_TO        = "avtookey12@mail.ru";    // куда слать заявки на почту ("" — не слать)
$TG_BOT_TOKEN   = "";                       // токен бота от @BotFather ("" — не слать в Telegram)
$TG_CHAT_ID     = "";                       // id чата/пользователя, куда бот пишет заявки
$MIN_INTERVAL   = 30;                       // минимум секунд между заявками с одного IP
// =====================

header("Content-Type: application/json; charset=UTF-8");

function reply($ok, $error = "", $code = 200) {
    http_response_code($code);
    echo json_encode(["ok" => $ok, "error" => $error], JSON_UNESCAPED_UNICODE);
    exit;
}

function field($name, $max) {
    $v = isset($_POST[$name]) ? trim((string)$_POST[$name]) : "";
    $v = preg_replace('/[\x00-\x1F\x7F]/u', ' ', $v); // без переводов строк и управляющих символов
    return mb_substr($v, 0, $max, "UTF-8");
}

if ($_SERVER["REQUEST_METHOD"] !== "POST") {
    reply(false, "Метод не поддерживается.", 405);
}

// Ловушка для ботов: скрытое поле должно быть пустым
if (field("website", 100) !== "") {
    reply(true);
}

$name    = field("name", 60);
$phone   = field("phone", 20);
$car     = field("car", 60);
$service = field("service", 60);
$comment = isset($_POST["comment"]) ? mb_substr(trim((string)$_POST["comment"]), 0, 1000, "UTF-8") : "";
$consent = field("consent", 1);

$digits = preg_replace('/\D/', '', $phone);
if (strlen($digits) < 10 || strlen($digits) > 12) {
    reply(false, "Проверьте номер телефона.", 400);
}
if ($consent !== "1") {
    reply(false, "Нужно согласие на обработку персональных данных.", 400);
}

// Простое ограничение частоты по IP
$ip = $_SERVER["REMOTE_ADDR"] ?? "";
$lock = sys_get_temp_dir() . "/avtookey_" . md5($ip);
if (is_file($lock) && time() - filemtime($lock) < $MIN_INTERVAL) {
    reply(false, "Заявка уже отправлена. Подождите немного или позвоните нам.", 429);
}
@touch($lock);

$text = "Новая заявка с сайта АвтоОкей\n\n"
      . "Имя: " . ($name !== "" ? $name : "—") . "\n"
      . "Телефон: {$phone}\n"
      . "Автомобиль: " . ($car !== "" ? $car : "—") . "\n"
      . "Услуга: " . ($service !== "" ? $service : "не выбрана") . "\n"
      . "Комментарий: " . ($comment !== "" ? $comment : "—") . "\n\n"
      . "Время: " . date("d.m.Y H:i");

$sent = false;

if ($TG_BOT_TOKEN !== "" && $TG_CHAT_ID !== "") {
    $ctx = stream_context_create(["http" => [
        "method"  => "POST",
        "header"  => "Content-Type: application/x-www-form-urlencoded",
        "content" => http_build_query(["chat_id" => $TG_CHAT_ID, "text" => $text]),
        "timeout" => 10,
    ]]);
    $res = @file_get_contents("https://api.telegram.org/bot{$TG_BOT_TOKEN}/sendMessage", false, $ctx);
    if ($res !== false && strpos($res, '"ok":true') !== false) {
        $sent = true;
    }
}

if ($MAIL_TO !== "") {
    $host = preg_replace('/[^a-z0-9.\-]/i', '', $_SERVER["HTTP_HOST"] ?? "localhost");
    $headers = implode("\r\n", [
        "MIME-Version: 1.0",
        "Content-Type: text/plain; charset=UTF-8",
        "From: =?UTF-8?B?" . base64_encode("Сайт АвтоОкей") . "?= <no-reply@{$host}>",
    ]);
    $subject = "=?UTF-8?B?" . base64_encode("Заявка с сайта: " . ($service !== "" ? $service : "ремонт")) . "?=";
    if (@mail($MAIL_TO, $subject, $text, $headers)) {
        $sent = true;
    }
}

if (!$sent) {
    reply(false, "Не удалось отправить заявку. Позвоните нам: +7 927 700-26-71", 500);
}

reply(true);
