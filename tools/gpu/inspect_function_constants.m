// Host-only metadata experiment. No guest ABI conclusions from this output.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <objc/runtime.h>
@protocol ConstantArrays
- (NSArray *)newNamedConstantArray;
- (NSArray *)newIndexedConstantArray;
@end
int main(void){@autoreleasepool{
    MTLFunctionConstantValues *values=[MTLFunctionConstantValues new];
    uint8_t input=63;[values setConstantValue:&input type:MTLDataTypeUChar withName:@"example"];
    for(Class c=object_getClass(values);c&&c!=NSObject.class;c=class_getSuperclass(c)){
        printf("CLASS %s\n",class_getName(c));unsigned n=0;
        Method *methods=class_copyMethodList(c,&n);
        for(unsigned i=0;i<n;i++)printf("METHOD %s %s\n",sel_getName(method_getName(methods[i])),method_getTypeEncoding(methods[i]));free(methods);
        Ivar *ivars=class_copyIvarList(c,&n);
        for(unsigned i=0;i<n;i++)printf("IVAR %s %s %td\n",ivar_getName(ivars[i]),ivar_getTypeEncoding(ivars[i]),ivar_getOffset(ivars[i]));free(ivars);
    }
    for(id value in [(id<ConstantArrays>)values newNamedConstantArray]) {
        NSLog(@"ELEMENT %@",value);Class c=object_getClass(value);unsigned n=0;
        Method *methods=class_copyMethodList(c,&n);
        for(unsigned i=0;i<n;i++)printf("ELEMENT_METHOD %s %s\n",sel_getName(method_getName(methods[i])),method_getTypeEncoding(methods[i]));free(methods);
        Ivar *ivars=class_copyIvarList(c,&n);
        for(unsigned i=0;i<n;i++)printf("ELEMENT_IVAR %s %s %td\n",ivar_getName(ivars[i]),ivar_getTypeEncoding(ivars[i]),ivar_getOffset(ivars[i]));free(ivars);
    }
}}
