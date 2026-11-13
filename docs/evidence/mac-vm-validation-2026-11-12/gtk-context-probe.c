#include <gtk/gtk.h>
#include <stdio.h>
int main(void) {
 gtk_init();
 GtkWidget *window=gtk_window_new(), *area=gtk_gl_area_new();
 gtk_gl_area_set_allowed_apis(GTK_GL_AREA(area),GDK_GL_API_GL);
 gtk_window_set_child(GTK_WINDOW(window),area);
 gtk_widget_realize(window);
 gtk_widget_realize(area);
 gtk_gl_area_make_current(GTK_GL_AREA(area));
 GError *error=gtk_gl_area_get_error(GTK_GL_AREA(area));
 int rc=0;
 if(error) {printf("context_created=0\nerror=%s\n",error->message);rc=2;}
 else {
  GdkGLContext *context=gtk_gl_area_get_context(GTK_GL_AREA(area));
  if(!context) {printf("context_created=0\nerror=no context and no GError\n");rc=3;}
  else {int major=0,minor=0;gdk_gl_context_get_version(context,&major,&minor);
   printf("context_created=1\napi=%u\nmajor=%d\nminor=%d\n",(unsigned)gdk_gl_context_get_api(context),major,minor);}
 }
 gtk_window_destroy(GTK_WINDOW(window));return rc;
}
