<?php
// Запросы к API бота MAX (https://dev.max.ru/docs-api).
// Новый адрес API подписан сертификатом Минцифры — он лежит рядом в max-ca.pem.

const MAX_API = "https://platform-api2.max.ru";

function max_request($token, $method, $path, $query = [], $body = null, $timeout = 15) {
    $url = MAX_API . $path . ($query ? "?" . http_build_query($query) : "");
    $ch = curl_init($url);
    $headers = ["Authorization: " . $token];
    if ($body !== null) {
        $headers[] = "Content-Type: application/json";
        curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($body, JSON_UNESCAPED_UNICODE));
    }
    curl_setopt_array($ch, [
        CURLOPT_CUSTOMREQUEST  => $method,
        CURLOPT_HTTPHEADER     => $headers,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT        => $timeout,
        CURLOPT_CAINFO         => __DIR__ . "/max-ca.pem",
    ]);
    $raw  = curl_exec($ch);
    $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $err  = curl_error($ch);
    curl_close($ch);
    return ["code" => $code, "data" => $raw !== false ? json_decode($raw, true) : null, "error" => $err];
}

function max_send($token, $chatId, $text) {
    $r = max_request($token, "POST", "/messages", ["chat_id" => $chatId], ["text" => $text]);
    return $r["code"] === 200;
}
