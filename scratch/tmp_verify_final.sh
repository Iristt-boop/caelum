#!/bin/bash
systemctl restart nox-core
sleep 5
journalctl -u nox-core --since "15 sec ago" --no-pager | grep -i location
