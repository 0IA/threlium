<?php

/**
 * threlium_default_folder — landing on the unified Virtual/All folder after login.
 *
 * Roundcube has no core setting for "which folder to open on startup" (it always
 * opens INBOX). The login_after hook returns the GET params Roundcube redirects to
 * right after a successful login, so we point it at the mail view of Virtual/All.
 * Normal folder navigation afterwards is unaffected.
 */
class threlium_default_folder extends rcube_plugin
{
    public $task = 'login|mail';

    public function init()
    {
        $this->add_hook('login_after', array($this, 'login_after'));
    }

    public function login_after($args)
    {
        $args['_task'] = 'mail';
        $args['_mbox'] = 'Virtual/All';
        return $args;
    }
}
