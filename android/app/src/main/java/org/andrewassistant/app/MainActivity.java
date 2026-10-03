package org.andrewassistant.app;
import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Bundle;
import android.webkit.*;
import android.widget.*;
import android.view.View;
import java.util.Arrays;

/** HTTPS-only foreground client. There is no native JS bridge or background mic. */
public class MainActivity extends Activity {
 private WebView web; private String server; private PermissionRequest pending;
 @Override public void onCreate(Bundle state) {
  super.onCreate(state);
  server=getPreferences(MODE_PRIVATE).getString("server","https://andrew.thegreatlakesgleaner.press");
  LinearLayout layout=new LinearLayout(this);layout.setOrientation(LinearLayout.VERTICAL);
  Button address=new Button(this);address.setText("Andrew · Change server");address.setOnClickListener(v->chooseServer());layout.addView(address);
  web=new WebView(this);layout.addView(web,new LinearLayout.LayoutParams(-1,0,1));setContentView(layout);
  web.getSettings().setJavaScriptEnabled(true);web.getSettings().setDomStorageEnabled(true);
  web.getSettings().setAllowFileAccess(false);web.getSettings().setAllowContentAccess(false);
  web.getSettings().setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
  web.getSettings().setMediaPlaybackRequiresUserGesture(false);
  CookieManager.getInstance().setAcceptThirdPartyCookies(web,false);
  web.setWebViewClient(new WebViewClient(){
   @Override public boolean shouldOverrideUrlLoading(WebView view,WebResourceRequest request){
    Uri u=request.getUrl();if(sameOrigin(u))return false;
    if("https".equals(u.getScheme()))startActivity(new Intent(Intent.ACTION_VIEW,u));return true;
   }
  });
  web.setWebChromeClient(new WebChromeClient(){
   @Override public void onPermissionRequest(PermissionRequest request){runOnUiThread(()->{
    if(!sameOrigin(request.getOrigin())||!Arrays.asList(request.getResources()).contains(PermissionRequest.RESOURCE_AUDIO_CAPTURE)){request.deny();return;}
    if(checkSelfPermission(Manifest.permission.RECORD_AUDIO)==PackageManager.PERMISSION_GRANTED){request.grant(new String[]{PermissionRequest.RESOURCE_AUDIO_CAPTURE});return;}
    if(pending!=null)pending.deny();pending=request;requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO},10);
   });}
   @Override public void onPermissionRequestCanceled(PermissionRequest request){if(pending==request)pending=null;}
  });
  if(state==null)web.loadUrl(server+"/");else web.restoreState(state);
 }
 private boolean sameOrigin(Uri uri){Uri expected=Uri.parse(server);return "https".equals(uri.getScheme())&&expected.getHost().equals(uri.getHost())&&expected.getPort()==uri.getPort();}
 private void chooseServer(){EditText input=new EditText(this);input.setText(server);new AlertDialog.Builder(this).setTitle("Your Andrew HTTPS address").setView(input).setPositiveButton("Connect",(dialog,which)->{
  Uri u=Uri.parse(input.getText().toString().trim());if(!"https".equals(u.getScheme())||u.getHost()==null||u.getUserInfo()!=null||u.getQuery()!=null||u.getFragment()!=null){Toast.makeText(this,"Use an HTTPS address without credentials.",Toast.LENGTH_LONG).show();return;}
  if(pending!=null){pending.deny();pending=null;}server=u.toString().replaceAll("/+$","");getPreferences(MODE_PRIVATE).edit().putString("server",server).apply();web.loadUrl(server+"/");
 }).setNegativeButton("Cancel",null).show();}
 @Override public void onRequestPermissionsResult(int code,String[] permissions,int[] results){super.onRequestPermissionsResult(code,permissions,results);if(code==10&&pending!=null){if(results.length>0&&results[0]==PackageManager.PERMISSION_GRANTED&&sameOrigin(pending.getOrigin()))pending.grant(new String[]{PermissionRequest.RESOURCE_AUDIO_CAPTURE});else pending.deny();pending=null;}}
 @Override protected void onPause(){web.evaluateJavascript("window.dispatchEvent(new Event('pagehide'))",null);web.onPause();super.onPause();}
 @Override protected void onResume(){super.onResume();if(web!=null)web.onResume();}
 @Override protected void onSaveInstanceState(Bundle out){web.saveState(out);super.onSaveInstanceState(out);}
 @Override public void onBackPressed(){if(web.canGoBack())web.goBack();else super.onBackPressed();}
 @Override protected void onDestroy(){if(pending!=null)pending.deny();web.destroy();super.onDestroy();}
}
