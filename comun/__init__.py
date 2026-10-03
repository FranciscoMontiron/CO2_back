"""Codigo compartido entre las apps de dominio.

No es una app de Django: no tiene modelos concretos ni migraciones. Existe para
que las enumeraciones del diagrama de clases vivan en un solo lugar, porque casi
todas se usan desde mas de una app y definirlas por duplicado seria la forma mas
rapida de que se desincronicen.
"""
