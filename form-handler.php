<?php
// Простой обработчик формы для отправки заявки на email

// Настройки
$to = "makrorobot77@gmail.com"; // рабочий email для заявок
$subject = "Новая заявка с лендинга LexGuard Business";

// Вспомогательная функция для безопасного получения поля
function field($name) {
    return isset($_POST[$name]) ? trim($_POST[$name]) : "";
}

$name    = field("name");
$phone   = field("phone");
$company = field("company");

if ($name === "" || $phone === "") {
    http_response_code(400);
    echo "Пожалуйста, заполните имя и телефон.";
    exit;
}

$ip   = $_SERVER["REMOTE_ADDR"] ?? "";
$ua   = $_SERVER["HTTP_USER_AGENT"] ?? "";
$host = $_SERVER["HTTP_HOST"] ?? "";
$uri  = $_SERVER["REQUEST_URI"] ?? "";

$bodyLines = [
    "Имя: {$name}",
    "Телефон: {$phone}",
    "Компания и ниша: " . ($company !== "" ? $company : "—"),
    "",
    "Источник: лендинг LexGuard Business ({$host}{$uri})",
    "IP: {$ip}",
    "User-Agent: {$ua}",
];

$body = implode("\r\n", $bodyLines);

$headers   = [];
$headers[] = "MIME-Version: 1.0";
$headers[] = "Content-Type: text/plain; charset=UTF-8";
$headers[] = "From: LexGuard Landing <no-reply@{$host}>";

@mail($to, "=?UTF-8?B?".base64_encode($subject)."?=", $body, implode("\r\n", $headers));

?>
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Заявка отправлена — LexGuard Business</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {
            margin: 0;
            font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            background: #0b1a33;
            color: #ffffff;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
        }
        .wrap {
            max-width: 420px;
            padding: 24px 20px 26px;
            border-radius: 18px;
            background: radial-gradient(circle at top left, #12264a, #050915);
            box-shadow: 0 18px 45px rgba(5, 12, 30, 0.6);
            text-align: left;
        }
        h1 {
            margin: 0 0 10px;
            font-size: 22px;
        }
        p {
            margin: 6px 0;
            font-size: 14px;
            color: #cbd5f5;
        }
        a {
            display: inline-flex;
            margin-top: 16px;
            padding: 10px 18px;
            border-radius: 999px;
            background: linear-gradient(135deg, #d4af37, #f5e3a3);
            color: #0b1a33;
            font-size: 14px;
            font-weight: 500;
            text-decoration: none;
        }
    </style>
</head>
<body>
    <div class="wrap">
        <h1>Заявка отправлена</h1>
        <p>Спасибо, <?=$name !== "" ? htmlspecialchars($name, ENT_QUOTES, "UTF-8") : "коллеги";?>.</p>
        <p>Мы получили вашу заявку и свяжемся с вами в рабочее время, чтобы уточнить детали и предложить формат юридического сопровождения.</p>
        <a href="index.html">Вернуться на сайт</a>
    </div>
</body>
</html>

