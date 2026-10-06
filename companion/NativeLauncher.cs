using System;
using System.Diagnostics;
using System.IO;
using System.Threading.Tasks;
class NativeLauncher {
 static bool ReadExact(Stream source,byte[] data) {
  int offset=0;while(offset<data.Length){int count=source.Read(data,offset,data.Length-offset);if(count==0)return false;offset+=count;}return true;
 }
 static void ForwardFrames(Stream source,Stream destination) {
  byte[] header=new byte[4];
  while(ReadExact(source,header)) {
   uint length=BitConverter.ToUInt32(header,0);if(length==0 || length>1048576)throw new IOException("Invalid native frame");
   byte[] body=new byte[(int)length];if(!ReadExact(source,body))throw new IOException("Incomplete native frame");
   destination.Write(header,0,4);destination.Write(body,0,body.Length);destination.Flush();
  }
 }
 static int Main(string[] args) {
  try {
   string folder=Path.GetDirectoryName(System.Reflection.Assembly.GetExecutingAssembly().Location);
   string[] config=File.ReadAllLines(Path.Combine(folder,"launcher.ini"));
   if(args.Length<1 || args[0]!=config[2])return 1;
   var info=new ProcessStartInfo(config[0],"\""+config[1]+"\" \""+args[0]+"\"");
   info.UseShellExecute=false;info.CreateNoWindow=true;info.RedirectStandardInput=true;info.RedirectStandardOutput=true;info.RedirectStandardError=true;
   var child=Process.Start(info);
   Task.Run(()=>{try{ForwardFrames(Console.OpenStandardInput(),child.StandardInput.BaseStream);}catch{}finally{child.StandardInput.Close();}});
   Task.Run(()=>{try{child.StandardError.ReadToEnd();}catch{}});
   ForwardFrames(child.StandardOutput.BaseStream,Console.OpenStandardOutput());child.WaitForExit();return child.ExitCode;
  }catch{return 1;}
 }
}
