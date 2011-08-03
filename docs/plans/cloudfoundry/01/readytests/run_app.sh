#!/bin/bash

cd $1 
vmc register --email foo@bar.com --passwd password
vmc login --email foo@bar.com --passwd password

vmc push env --instances 4 --mem 64M --url env.vcap.me -n

# the above may already be there and i cannot seem to clean them up
set -e
curl  http://env.vcap.me | grep "Hello from the Cloud"
vmc apps | grep 'env.vcap.me'