#import <Foundation/Foundation.h>
#import <AppKit/AppKit.h>
#import <Vision/Vision.h>
int main(int argc,const char *argv[]){@autoreleasepool{
 if(argc!=2){fprintf(stderr,"Usage: review_vision jobs.json\n");return 2;}
 NSArray *jobs=[NSJSONSerialization JSONObjectWithData:[NSData dataWithContentsOfFile:@(argv[1])] options:0 error:nil];
 if(![jobs isKindOfClass:[NSArray class]]){fprintf(stderr,"Invalid jobs JSON\n");return 2;}
 for(NSDictionary *job in jobs){@autoreleasepool{
 NSString *output=job[@"output"];if([[NSFileManager defaultManager] fileExistsAtPath:output])continue;
 NSImage *image=[[NSImage alloc]initWithContentsOfFile:job[@"image"]];CGImageRef im=[image CGImageForProposedRect:NULL context:nil hints:nil];
 if(!im){NSLog(@"Missing image: %@",job[@"image"]);return 2;}
 VNRecognizeTextRequest *req=[VNRecognizeTextRequest new];req.recognitionLevel=VNRequestTextRecognitionLevelAccurate;req.usesLanguageCorrection=NO;req.recognitionLanguages=@[@"en-US"];req.usesCPUOnly=YES;
 NSError *err=nil;[[[VNImageRequestHandler alloc]initWithCGImage:im options:@{}] performRequests:@[req] error:&err];
 if(err){NSLog(@"%@",err);return 1;}
 NSMutableArray *lines=[NSMutableArray new];
 for(VNRecognizedTextObservation *ob in req.results){VNRecognizedText *c=[ob topCandidates:1].firstObject;CGRect b=ob.boundingBox;[lines addObject:@{@"text":c.string,@"confidence":@(c.confidence),@"bbox":@[@(b.origin.x),@(1-CGRectGetMaxY(b)),@(CGRectGetMaxX(b)),@(1-b.origin.y)]}];}
 [[NSJSONSerialization dataWithJSONObject:lines options:NSJSONWritingPrettyPrinted error:nil]writeToFile:output atomically:YES];
 NSLog(@"%@ : %lu lines",job[@"image"],(unsigned long)lines.count);
 }}
}return 0;}
