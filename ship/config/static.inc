# Static bones site. Security headers come from the server-level include; locations here use
# `expires` (not add_header), so they keep inheriting them.
index index.html;
error_page 404 /404.html;

location / {
    limit_req zone=perip burst=40 nodelay;
    try_files $uri $uri/ =404;
    expires -1;
}
location = /404.html {
    internal;
}
location ^~ /assets/ {
    try_files $uri =404;
    expires 1d;
}
location = /favicon.ico {
    expires 7d;
}
location ~ /\. {
    deny all;
}
