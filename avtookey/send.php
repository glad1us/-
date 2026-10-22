<?php
// Обработчик заявки с сайта АвтоОкей.
// Отправляет заявку в MAX (если настроен бот) и/или на почту. Настройки — в config.php.

require __DIR__ . "/config.php";
require __DIR__ . "/max.php";

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

$text = "Новая заявка с сайта АвтоОкей\n\n"
      . "Имя: " . ($name !== "" ? $name : "—") . "\n"
      . "Телефон: {$phone}\n"
      . "Автомобиль: " . ($car !== "" ? $car : "—") . "\n"
      . "Услуга: " . ($service !== "" ? $service : "не выбрана") . "\n"
      . "Комментарий: " . ($comment !== "" ? $comment : "—") . "\n\n"
      . "Время: " . date("d.m.Y H:i");

$sent = false;

if ($MAX_BOT_TOKEN !== "") {
    foreach ($MAX_CHAT_IDS as $chatId) {
        if (max_send($MAX_BOT_TOKEN, $chatId, $text)) {
            $sent = true;
        }
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

@touch($lock);
reply(true);
