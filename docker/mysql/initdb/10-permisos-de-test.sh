#!/bin/bash
# ---------------------------------------------------------------------------
#  Permisos sobre las bases de prueba.
#
#  Django crea una base `test_<NAME>` para correr la suite y la destruye al
#  terminar. El usuario de la aplicacion no tiene privilegio para crearla, asi
#  que `pytest` falla con "Access denied ... to database 'test_co2'" aunque la
#  aplicacion funcione perfectamente.
#
#  Se concede sobre el patron `test\_%` y no sobre una base puntual para que
#  tambien sirva con `pytest -n` (xdist), que usa test_co2_gw0, _gw1, etc.
#
#  Va como .sh y no como .sql porque asi toma el usuario de MYSQL_USER en vez
#  de hardcodearlo: si el equipo cambia el usuario en .env, esto lo sigue.
#
#  OJO: el entrypoint de MySQL corre este directorio UNA SOLA VEZ, al
#  inicializar un volumen vacio. Sobre un volumen existente hay que aplicarlo a
#  mano o recrearlo con `docker compose down -v`.
# ---------------------------------------------------------------------------
set -eu

mysql --protocol=socket -uroot -p"${MYSQL_ROOT_PASSWORD}" <<SQL
GRANT ALL PRIVILEGES ON \`test\_%\`.* TO '${MYSQL_USER}'@'%';
FLUSH PRIVILEGES;
SQL

echo "Permisos de test concedidos a '${MYSQL_USER}' sobre test\_%"
