#!/usr/bin/env python

import sys
import os
import tempfile
import getpass
import simplejson as json
import uuid

f = open("bootconf.json", "r")
vals_dict = json.load(f)
f.close()

(osf, fname) = tempfile.mkstemp()
uu = str(uuid.uuid4())
web_message = "%s :: %s" % (vals_dict['message'], uu)
print web_message
os.write(osf, web_message)
os.close(osf)
sudo = ""
if getpass.getuser() != "root":
    sudo = "sudo"
cmd = "%s cp %s /var/www/test.txt && %s chmod 644 /var/www/test.txt" % (sudo, fname, sudo)
print cmd
rc = os.system(cmd)

out_vals = {}
out_vals = {'testpgm' : 'hello' }
out_vals = {'webmessage' : web_message }
output = open('bootout.json', "w")
json.dump(out_vals, output)
output.close()
print "outvals %s" %(str(out_vals))

sys.exit(rc)

