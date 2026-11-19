# Let's Encrypt http-01 challenges (acme-client writes them to /var/www/acme).
location ^~ /.well-known/acme-challenge/ {
    alias /var/www/acme/;
    try_files $uri =404;
}
